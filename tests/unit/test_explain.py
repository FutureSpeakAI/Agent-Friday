"""A13: why did Friday do that (NS-29.1-1, services/explain).

  * a seeded outward action (its decision, outcome, send, task journal
    events, file made, card and grant, model call and prompt sections)
    answers every question: what, why, which model, what information, what
    left this PC, on whose authority, what it cost, what changed, and its
    linked records, by ids, names and counts only: nothing a record says (no
    content, path, address or section name) reaches the answer;
  * the same, asked of the task;
  * a bare decision says "not recorded" for each piece nothing kept, and an
    id nothing names says so;
  * a section that fails, or a store that fails, is named and the others are
    still answered;
  * an id that is not one action id or one task id is refused before any
    store is read; one explanation at a time; out of time is said, never
    raised;
  * a decision's detail is a fixed code, never a word of it;
  * a task's model calls are counted once (the task journal's when it has
    any, else the activity rows), tokens likewise; the task index and the
    approvals store are read once per explanation; a card is found by
    approval or approval_id; the links trace is not started with under
    LINKS_MIN_S left, and no costly read starts once the total is spent;
  * this turn's context manifests are told apart from the action's own sends;
  * the Ledger page's "Why?" sets the answer with textContent only, and a
    late answer never overwrites a newer one.
Synthetic records only; ids are made at run time.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from agent_friday.services import artifact_manifest as am
from agent_friday.services import explain as E
from agent_friday.services import goals
from agent_friday.services import task_journal as tj
from agent_friday.services import unified_ledger as U

ROOT = Path(__file__).resolve().parents[2]
WORDS = "SYNTHETIC-A13-CONTENT-MARKER"
TID = "a13-explain-task-0001"
TURN = "turn_a13_probe_0001"
CARD = "apr_a13_probe_0001"
TOOL = "probe:a13_send"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from agent_friday.services import egress_gate, idempotency_index, outward_audit
    from agent_friday.services import outcome_memory, retrieval_ledger
    monkeypatch.setattr(outcome_memory, "DIR_OVERRIDE", tmp_path / "outcomes")
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": False})
    tj.reset_for_tests()
    tj.clear_action()
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(G, "verify_claws", lambda *a, **k: (True, "intact"))
    monkeypatch.setattr(G, "outward_hold", lambda: None)
    monkeypatch.setattr(G, "_send_grant", lambda *a, **k: None)
    monkeypatch.setattr(G, "_grants_file", lambda: tmp_path / "governance" / "grants.json")
    (tmp_path / "governance").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(am, "manifest_path", lambda: tmp_path / "artifact-manifest.jsonl")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "vault" / "egress-log.jsonl")
    monkeypatch.setattr(retrieval_ledger, "_DEFAULT_LOG", tmp_path / "vault" / "retrieval-log.jsonl")
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    idempotency_index.reset()
    outward_audit.reset_cache()
    U.reset_cache()
    yield
    U.reset_cache()
    idempotency_index.reset()
    outward_audit.reset_cache()
    from agent_friday.services import action_journal
    action_journal.reset()


def _seed(tmp_path) -> dict:
    """One outward action with every record Friday keeps about it, each
    through its own writer (or, for the card, its store's shape)."""
    from agent_friday.services import activity_ledger, egress_gate, retrieval_ledger

    tj.append(TID, "created", name=WORDS)
    tj.index_put(TID, WORDS, "running", time.time())
    # the card the owner approved, and the grant the task runs under
    approvals.APPROVALS_FILE.write_text(json.dumps([{
        "approval_id": CARD, "kind": "send_email", "subject_type": "task", "subject_id": TID,
        "title": WORDS, "description": WORDS, "payload": {"to": WORDS.lower() + "@example.invalid"},
        "status": "approved", "decided_source": "owner_press", "policy_class": "outward",
        "policy_version": "pv_a13_probe", "decided_policy_version": "pv_a13_probe",
        "cost_estimate_mψ": 7, "created_at": time.time()}]), encoding="utf-8")
    grant = G.create_grant(tools=[TOOL], scope=TID, expires_in_seconds=3600, max_uses=2, note=WORDS,
                           limits={"destination_scope": [WORDS.lower() + ".invalid"]})
    # the decision, as the gate writes it (its envelope counts where the
    # arguments came from)
    env = G.build_envelope(TOOL, klass="outward", decision="allow", ctx={"task_id": TID},
                           provenance={"user": 1, "content": 2})
    dec = G._receipt({"tool": TOOL, "class": "outward", "decision": "allow",
                      "reason": "pre-approved grant %s for %s" % (grant["grant_id"], WORDS),
                      "surface": "background", "approval": CARD, "grant": grant["grant_id"],
                      "task_id": TID, "turn_id": TURN, "target": WORDS, "envelope": env.to_dict()})
    aid = dec["rec"]["action_id"]
    # what it did: the outcome, the task journal's receipt and the outcome memory
    tj.push_task(TID)
    try:
        G.record_outcome(TOOL, {"seq": dec["seq"], "hash": dec["hash"], "action_id": aid,
                                "class": "outward", "approval": CARD,
                                "policy_version": dec["rec"].get("policy_version")},
                         "ok", outcome="completed_verified")
    finally:
        tj.pop_task()
    tj.tool_call(name=TOOL, args={"to": WORDS}, result=WORDS, ok=True, task_id=TID)
    # the send, in the egress ledger (and its context manifest)
    sent = egress_gate.record_service_send("mail", "probe", destination=WORDS.lower() + ".invalid",
                                           content=None, action_id=aid)
    # the model that ran for the turn and the task, and what the task's calls cost
    activity_ledger.record("model_invocation", model="probe-model-a13", provider="probe-provider",
                           seat="cloud", tokens_in=120, tokens_out=30, turn_id=TURN, task_id=TID)
    tj.model_call(model="probe-model-a13", provider="probe-provider", seat="cloud", tokens_in=120,
                  tokens_out=30, cost_usd=0.0042, task_id=TID)
    # the prompt sections the turn assembled
    log = Path(retrieval_ledger._DEFAULT_LOG)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "turn_id": TURN, "section": WORDS, "tier": 2,
                            "chars": 50, "planner": None, "applied": False}) + "\n")
    # a file made under the decision, and the task journal's note of it
    made = tmp_path / ("%s.md" % WORDS)
    made.write_bytes(WORDS.encode())
    line = am._sign({"kind": "artifact", "ts": "2026-10-30T10:00:00Z", "path": str(made.resolve()),
                     "sha256": hashlib.sha256(WORDS.encode()).hexdigest(), "bytes": len(WORDS),
                     "tool": "write_file", "task_id": TID, "turn_id": None, "args_hash": None,
                     "receipt": {"hmac": dec["rec"].get("hmac"), "decision": "allow"},
                     "provenance": False, "qa": {"checked": True, "ok": True, "findings": [WORDS]}})
    with open(am.manifest_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    tj.artifact(TID, am.artifact_ref(line), tool="write_file", qa_ok=True, action_id=aid)
    return {"action_id": aid, "grant_id": grant["grant_id"], "call_id": sent["call_id"],
            "manifest_id": sent["manifest_id"], "decision_sig": dec["rec"].get("hmac") or ""}


def _dump(out) -> str:
    return json.dumps(out, sort_keys=True)


def _no_content(out, tmp_path, ids=None) -> None:
    text = _dump(out)
    assert WORDS.lower() not in text.lower(), "no content, name, address or section name"
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "\\\\") not in text
    assert "invalid" not in text and "@" not in text, "no recipient or destination"
    if ids and ids.get("decision_sig"):
        assert ids["decision_sig"][:16] not in text, "no signature's characters"


# ── 1. every section, ids and counts only ────────────────────────────────────

def test_a_seeded_outward_action_answers_every_question_with_ids_and_counts_only(tmp_path):
    ids = _seed(tmp_path)
    aid = ids["action_id"]
    out = E.explain(action_id=aid)
    s = out["sections"]
    assert out["found"] is True and out["subject"] == {"action_id": aid}
    assert out["order"] == list(E.SECTION_NAMES) and set(s) == set(E.SECTION_NAMES)
    assert {n: s[n]["status"] for n in E.SECTION_NAMES} == {n: "recorded" for n in E.SECTION_NAMES}
    # what
    a = s["what"]["action"]
    assert (a["action_id"], a["tool"], a["decision"], a["outcome"]) == (aid, TOOL, "allow", "completed_verified")
    assert a["decided_at"] and a["closed_at"]
    # why: a reason code, never the reason's words; the envelope's counts
    w = s["why"]["decision"]
    assert w["reason_code"] == "pre_approved_grant" and w["origin"] == "task"
    assert w["risk_class"] == "outward"
    assert w["provenance"] == {"owner": 1, "content": 2, "sender": 0, "model": 0}
    assert "signed_receipt" in w["checks"]
    # which model: the turn's model_invocation row
    m = s["model"]
    assert m["linked_by"] == "turn"
    assert [(x["model"], x["provider"], x["seat"]) for x in m["models"]] == \
        [("probe-model-a13", "probe-provider", "cloud")]
    # what information: the send's manifest and the turn's sections, counted
    info = s["information"]
    assert info["manifests"] == 1 and info["manifest_ids"] == [ids["manifest_id"]]
    assert info["sensitivity_classes"] == {"SERVICE": 1} and info["gate_actions"] == {"allow": 1}
    assert info["turn_sections"] == {"count": 1, "by_tier": {"tier_2": 1}, "sent": 1}
    # what left: by channel, with its ids, no destination
    eg = s["egress"]
    assert eg["by_channel"] == {"mail": 1} and eg["by_result"] == {"sent": 1}
    assert eg["call_ids"] == [ids["call_id"]] and eg["manifest_ids"] == [ids["manifest_id"]]
    # authority: the card and the grant, the fields that limit it (names only)
    au = s["authority"]
    assert [c["card_id"] for c in au["cards"]] == [CARD]
    assert au["cards"][0]["status"] == "approved" and au["cards"][0]["decided_by"] == "owner_press"
    g = au["grants"][0]
    assert g["grant_id"] == ids["grant_id"] and g["found"] is True and g["status"] == "active"
    assert g["limited_by"] == ["expires_at", "max_uses", "tools", "destination_scope"]
    assert "used" not in g and "max_uses" not in g, "names of the limiting fields only, never values"
    assert au["policy_versions"] and au["origins"] == {"task": 1} and au["owner_route"] is False
    # cost: what the task journal recorded, and the card's estimate
    c = s["cost"]
    assert c["usd_recorded"] == pytest.approx(0.0042) and c["priced_calls"] == 1
    assert c["card_estimate_mpsi"] == 7 and c["tokens"]["in"] >= 120
    # what changed: the outcome receipt, the file (keyed), the journal receipt, the outcome memory
    ch = s["changed"]
    assert [o["outcome"] for o in ch["outcomes"]] == ["completed_verified"]
    assert ch["outcomes"][0]["receipt_id"].startswith("lgr_")
    assert len(ch["artifact_ids"]) == 1 and ch["artifact_ids"][0].startswith("art_")
    assert ch["journal_receipt_ids"] and all(r.startswith("lgr_") for r in ch["journal_receipt_ids"])
    assert ch["outcome_memory"] and ch["outcome_memory"][0]["id"].startswith("out_")
    assert ch["outcome_memory"][0]["result"] == "succeeded"
    # links: the unified ledger's trace
    ln = s["links"]
    assert {"decision", "outcome", "egress", "journal"} <= set(ln["families"])
    assert all(set(r) == {"family", "kind", "link", "ts", "ids"} for r in ln["rows"])
    # every section says itself in plain words, and nothing a record says
    assert all(s[n]["said"].startswith(E.SECTION_LABELS[n]) for n in E.SECTION_NAMES)
    assert out["text"].startswith("Action " + aid)
    _no_content(out, tmp_path, ids)


def test_a_task_is_explained_across_its_actions(tmp_path):
    ids = _seed(tmp_path)
    out = E.explain(task_id=TID)
    s = out["sections"]
    assert out["found"] is True and out["subject"] == {"task_id": TID}
    assert s["what"]["task_status"] == "running"
    assert [x["action_id"] for x in s["what"]["actions"]] == [ids["action_id"]]
    assert s["what"]["tools"] == {TOOL: 1}
    assert s["why"]["by_reason"] == {"pre_approved_grant": 1}
    assert s["egress"]["by_channel"] == {"mail": 1}
    assert s["cost"]["usd_recorded"] == pytest.approx(0.0042)
    assert s["changed"]["artifact_ids"] and s["changed"]["outcome_memory"]
    _no_content(out, tmp_path, ids)


# ── 2. honest gaps ───────────────────────────────────────────────────────────

def test_a_bare_decision_says_not_recorded_for_every_piece_nothing_kept(tmp_path):
    dec = G._receipt({"tool": "probe:a13_bare", "class": "outward", "decision": "allow",
                      "surface": "test", "target": WORDS})
    aid = dec["rec"]["action_id"]
    out = E.explain(action_id=aid)
    s = out["sections"]
    assert out["found"] is True
    assert s["what"]["status"] == "recorded" and s["what"]["action"]["outcome"] is None
    assert E.NOT_RECORDED in s["what"]["said"], "no outcome receipt: said, not guessed"
    for n in ("model", "information", "egress", "cost", "changed"):
        assert s[n]["status"] == "not_recorded", (n, s[n])
        assert E.NOT_RECORDED in s[n]["said"], (n, s[n]["said"])
    assert not s["authority"].get("cards") and not s["authority"].get("grants")
    assert E.NOT_RECORDED in s["authority"]["said"]
    assert E.NOT_RECORDED in s["why"]["said"], "no reason given: not recorded"
    _no_content(out, tmp_path)


def test_an_id_nothing_names_says_so_in_every_section():
    out = E.explain(action_id="act_a13_nothing_names_this")
    assert out["found"] is False
    assert out["text"].startswith("Action act_a13_nothing_names_this: nothing Friday keeps names this id.")
    assert all(sec["status"] == "not_recorded" for sec in out["sections"].values()), out["sections"]


# ── 3. one failure hides nothing else ────────────────────────────────────────

def test_a_failing_section_is_named_and_the_others_are_still_answered(tmp_path, monkeypatch):
    ids = _seed(tmp_path)

    def broken(run):
        raise OSError("cannot open %s" % (tmp_path / WORDS))
    monkeypatch.setattr(E, "SECTIONS", [(n, broken if n == "model" else f) for n, f in E.SECTIONS])
    out = E.explain(action_id=ids["action_id"])
    s = out["sections"]
    assert s["model"]["status"] == "error" and s["model"]["error"] == "OSError"
    assert all(s[n]["status"] == "recorded" for n in E.SECTION_NAMES if n != "model")
    _no_content(out, tmp_path, ids)


def test_a_failing_store_fails_only_the_sections_that_need_it(tmp_path, monkeypatch):
    ids = _seed(tmp_path)

    def broken(run):
        raise OSError("cannot open %s" % (tmp_path / WORDS))
    monkeypatch.setattr(E, "_read_egress", broken)
    out = E.explain(action_id=ids["action_id"])
    s = out["sections"]
    assert s["egress"]["status"] == "error" and s["egress"]["error"] == "OSError"
    for n in ("what", "why", "model", "authority", "cost", "changed", "links"):
        assert s[n]["status"] == "recorded", (n, s[n])
    _no_content(out, tmp_path, ids)


# ── 4. refused ids, one at a time, bounded ───────────────────────────────────

@pytest.mark.parametrize("kw", [
    {}, {"action_id": "act_a13_x", "task_id": TID}, {"action_id": "..\\..\\" + WORDS},
    {"task_id": "tasks/" + TID}, {"task_id": "a13..up"}, {"action_id": "a" * 200},
    {"action_id": WORDS.lower() + "@example.invalid"}, {"task_id": "has space"},
])
def test_an_id_that_is_not_one_action_or_task_id_is_refused_before_any_read(kw, monkeypatch):
    def no_read(*a, **k):
        raise AssertionError("no store is read for a refused id")
    monkeypatch.setattr(L, "entries", no_read)
    with pytest.raises(E.BadId):
        E.explain(**kw)


def test_one_explanation_at_a_time(monkeypatch):
    held = threading.Lock()
    held.acquire()                     # another explanation holds it
    monkeypatch.setattr(E, "_RUN_LOCK", held)
    monkeypatch.setattr(E, "BUSY_WAIT_S", 0.01)
    try:
        with pytest.raises(E.Busy):
            E.explain(action_id="act_a13_busy_probe")
    finally:
        held.release()


def test_out_of_time_is_said_and_never_raised(tmp_path, monkeypatch):
    ids = _seed(tmp_path)
    monkeypatch.setattr(E, "TOTAL_BUDGET_S", 0.0)
    out = E.explain(action_id=ids["action_id"])
    assert out["out_of_time"] is True
    assert all(sec["status"] == "out_of_time" for sec in out["sections"].values())


def test_reason_codes_never_carry_the_reasons_words():
    assert E.reason_code("held: " + WORDS) == "held"
    assert E.reason_code("approved card") == "approved_card"
    assert E.reason_code(WORDS) == "other"
    assert E.reason_code("") is None
    assert E.reason_code("mail_send", "mail_send") == "carried_out_by_executor"


def test_a_decisions_detail_is_a_fixed_code_never_a_word_of_it(tmp_path):
    word = "a13_probe_detail_word"            # a code-shaped word: _code would have echoed it
    assert E.detail_code("duplicate_key") == "duplicate_key"
    assert E.detail_code(word) == "other" and E.detail_code(WORDS) == "other"
    assert E.detail_code({"k": word}) == "other"
    assert E.detail_code(None) is None and E.detail_code("") is None
    assert E._why_one({"detail": word})["detail"] == "other"
    dec = G._receipt({"tool": "probe:a13_detail", "class": "outward", "decision": "allow",
                      "surface": "test", "detail": word})
    out = E.explain(action_id=dec["rec"]["action_id"])
    assert out["sections"]["why"]["decision"]["detail"] in (None, "other")
    assert word not in _dump(out)


# ── 5. counted once, read once, bounded ──────────────────────────────────────

def test_a_task_with_journal_and_activity_model_rows_counts_each_call_once(tmp_path):
    from agent_friday.services import activity_ledger
    tid = "a13-explain-task-0002"
    tj.append(tid, "created", name=WORDS)
    tj.index_put(tid, WORDS, "running", time.time())
    # the same call, as the activity record and as the task journal keep it
    activity_ledger.record("model_invocation", model="probe-model-a13", provider="probe-provider",
                           seat="cloud", tokens_in=120, tokens_out=30, task_id=tid)
    tj.model_call(model="probe-model-a13", provider="probe-provider", seat="cloud", tokens_in=120,
                  tokens_out=30, cost_usd=0.0042, task_id=tid)
    out = E.explain(task_id=tid)
    m = out["sections"]["model"]
    assert m["linked_by"] == "task" and m["counted_from"] == "task_journal"
    assert [(x["calls"], x["tokens_in"], x["tokens_out"]) for x in m["models"]] == [(1, 120, 30)]
    c = out["sections"]["cost"]
    assert c["tokens"] == {"in": 120, "out": 30} and c["model_calls"] == 1 and c["priced_calls"] == 1
    _no_content(out, tmp_path)


def test_a_task_with_activity_rows_only_counts_them(tmp_path):
    from agent_friday.services import activity_ledger
    tid = "a13-explain-task-0003"
    tj.append(tid, "created", name=WORDS)
    tj.index_put(tid, WORDS, "running", time.time())
    activity_ledger.record("model_invocation", model="probe-model-a13", provider="probe-provider",
                           seat="cloud", tokens_in=50, tokens_out=5, task_id=tid)
    out = E.explain(task_id=tid)
    m = out["sections"]["model"]
    assert m["status"] == "recorded" and m["linked_by"] == "task" and m["counted_from"] == "activity"
    assert [(x["calls"], x["tokens_in"], x["tokens_out"]) for x in m["models"]] == [(1, 50, 5)]
    assert out["sections"]["cost"]["tokens"] == {"in": 50, "out": 5}


def test_the_links_section_is_not_started_with_under_a_second_left(tmp_path, monkeypatch):
    ids = _seed(tmp_path)
    started = []

    def trace(**kw):
        started.append(kw)
        raise AssertionError("the trace is not started this late")
    monkeypatch.setattr(U, "unified", trace)
    monkeypatch.setattr(E._Run, "left", lambda self: E.LINKS_MIN_S / 2)
    out = E.explain(action_id=ids["action_id"])
    assert started == []
    assert out["sections"]["links"]["status"] == "out_of_time"
    assert out["out_of_time"] is True
    assert out["sections"]["what"]["status"] == "recorded", "the other sections are still read"
    assert E.WORST_CASE_S == E.BUSY_WAIT_S + E.TOTAL_BUDGET_S - E.LINKS_MIN_S + U.BUSY_WAIT_S + U.TOTAL_BUDGET_S
    assert out["bounded"]["worst_case_seconds"] == E.WORST_CASE_S


def test_a_costly_read_is_not_started_once_the_total_is_spent(tmp_path, monkeypatch):
    ids = _seed(tmp_path)
    run = E._Run(ids["action_id"], None)
    monkeypatch.setattr(E._Run, "left", lambda self: 0.0)
    with pytest.raises(E.OutOfTime):
        E._read_cards(run)
    with pytest.raises(E.OutOfTime):
        E._read_task_index(run)
    assert {"approvals", "task_index"} <= run.partial


def test_the_task_index_and_the_approvals_store_are_read_once_per_explanation(tmp_path, monkeypatch):
    ids = _seed(tmp_path)
    tid2 = "a13-explain-task-0004"
    tj.append(tid2, "created", name=WORDS)
    tj.index_put(tid2, WORDS, "running", time.time())
    # the same action decided again under a second task: two tasks to look up
    G._receipt({"tool": TOOL, "class": "outward", "decision": "allow", "surface": "test",
                "action_id": ids["action_id"], "task_id": tid2, "approval": CARD})
    reads = {"index": 0, "approvals": 0}
    real_index, real_store = tj.index_read, approvals._read_store

    def index_read():
        reads["index"] += 1
        return real_index()

    def read_store():
        reads["approvals"] += 1
        return real_store()
    monkeypatch.setattr(tj, "index_read", index_read)
    monkeypatch.setattr(approvals, "_read_store", read_store)
    monkeypatch.setattr(U, "unified", lambda **kw: {"rows": []})   # the trace reads on its own
    out = E.explain(action_id=ids["action_id"])
    s = out["sections"]
    assert s["what"]["status"] == "recorded" and s["authority"]["cards"][0]["card_id"] == CARD
    assert s["cost"]["card_estimate_mpsi"] == 7, "the card is found in the one read"
    assert reads == {"index": 1, "approvals": 1}, reads


def test_the_cost_section_finds_a_card_named_by_approval_id(tmp_path):
    card = "apr_a13_probe_0002"
    approvals.APPROVALS_FILE.write_text(json.dumps([{
        "approval_id": card, "kind": "send_email", "subject_type": "task", "subject_id": TID,
        "title": WORDS, "status": "pending", "cost_estimate_mψ": 5, "created_at": time.time()}]),
        encoding="utf-8")
    dec = G._receipt({"tool": "probe:a13_card", "class": "outward", "decision": "allow",
                      "surface": "test", "approval_id": card})
    out = E.explain(action_id=dec["rec"]["action_id"])
    s = out["sections"]
    assert [c["card_id"] for c in s["authority"]["cards"]] == [card]
    assert s["cost"]["card_estimate_mpsi"] == 5
    _no_content(out, tmp_path)


def test_this_turns_manifests_are_told_apart_from_the_actions_own_sends(tmp_path):
    from agent_friday.services import context_manifest as cm
    ids = _seed(tmp_path)
    turn_mid = "ctx_a13_turn_probe_0001"
    p = Path(cm.path())
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"manifest_id": turn_mid, "turn_id": TURN, "ts": time.time(),
                            "sensitivity_classes": ["MEMORY"],
                            "sources": [{"action": "allow", "count": 2, "part": WORDS}],
                            "withheld": 1, "estimated_tokens": 40}) + "\n")
    out = E.explain(action_id=ids["action_id"])
    info = out["sections"]["information"]
    assert info["by_link"] == {"send": 1, "turn": 1}
    assert info["sends"]["manifest_ids"] == [ids["manifest_id"]]
    assert info["turn"]["manifest_ids"] == [turn_mid]
    assert info["turn"]["sensitivity_classes"] == {"MEMORY": 1} and info["turn"]["parts"] == 2
    assert info["sends"]["sensitivity_classes"] == {"SERVICE": 1}
    said = info["said"]
    assert "1 context manifest of this action's own sends" in said
    assert "this turn's context manifest (1), named by the turn and not by a send of this action" in said
    _no_content(out, tmp_path, ids)


# ── 6. the Ledger page ───────────────────────────────────────────────────────

def test_the_ledger_page_asks_why_and_shows_the_answer_with_textcontent_only():
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert 'id="u-why"' in html and "Why?" in html
    start = html.index("function explainShow")
    body = html[start:html.index("function unifiedRender", start)]
    assert "/api/ledger/explain?" in body and "textContent" in body
    assert "action_id" in body and "task_id" in body
    # a late answer never overwrites a newer one: each press is numbered and
    # both the answer and the failure check the number before writing
    assert "let explainSeq = 0;" in html and "const mine = ++explainSeq;" in body
    assert body.count("if (mine !== explainSeq) return;") == 2
    render = html[html.index("function unifiedRender"):html.index("function unifiedLoad")]
    assert "whyButton('action', rid.action_id)" in render and "whyButton('task', rid.task_id)" in render
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in html, sink
