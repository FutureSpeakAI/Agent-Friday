"""A12: every receipt family together (NS-37.7-1, services/unified_ledger).

  * every registered family appears, each row in the one shape (family, kind,
    at, ids, signed, chained, where, summary), seeded through the family's
    own writer, and nothing a record says (no content, path or recipient)
    reaches the list;
  * a source that fails or runs out of time is named and the others are
    still listed;
  * filter by family;
  * an action id traces across families: its decision, its outcome, the
    task journal's receipt and the task's other events, its send in the
    egress ledger, the goal receipt of the goal its task was checked for;
  * paging by cursor is stable while newer records arrive;
  * the Ledger page's "Everything, together" section sets text with
    textContent only.
Synthetic records only; ids are made at run time.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from agent_friday.governance import action_gate as G
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals
from agent_friday.services import artifact_manifest as am
from agent_friday.services import goals
from agent_friday.services import task_journal as tj
from agent_friday.services import unified_ledger as U

ROOT = Path(__file__).resolve().parents[2]
WORDS = "SYNTHETIC-A12-CONTENT-MARKER"
TID = "a12-unified-task-0001"
GID = "goal_a12_probe_0001"
ROW_KEYS = {"family", "kind", "at", "ts", "ids", "signed", "chained", "where", "summary",
            "source", "ref"}
ID_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+\-]{0,127}")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from agent_friday.services import content_pipeline, egress_gate, idempotency_index
    from agent_friday.services import outward_audit, provenance, setup_record
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
    monkeypatch.setattr(am, "manifest_path", lambda: tmp_path / "artifact-manifest.jsonl")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "vault" / "egress-log.jsonl")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", tmp_path / "provenance")
    monkeypatch.setattr(provenance, "LEDGER_FILE", tmp_path / "provenance" / "ledger.jsonl")
    monkeypatch.setattr(content_pipeline, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(setup_record, "ledger_path", lambda: tmp_path / "setup" / "ledger.jsonl")
    monkeypatch.setattr(U, "_memory_ledger_path", lambda: tmp_path / "memory" / "memory_ledger.jsonl")
    monkeypatch.setattr(U, "_skills_root", lambda: tmp_path / "skills")
    monkeypatch.setattr(goals, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(goals, "REVIEWS_DIR", tmp_path / "goals" / "reviews")
    monkeypatch.setattr(goals, "RECEIPTS_LOG", tmp_path / "governance" / "goal_receipts.jsonl")
    idempotency_index.reset()
    outward_audit.reset_cache()
    yield
    idempotency_index.reset()
    outward_audit.reset_cache()
    from agent_friday.services import action_journal
    action_journal.reset()


def _seed(tmp_path) -> dict:
    """One record of every family, each through its own writer. Returns the
    ids the trace test follows."""
    from agent_friday import skill_registry
    from agent_friday.cognitive_memory import CognitiveMemory
    from agent_friday.services import action_journal, activity_ledger, content_pipeline
    from agent_friday.services import egress_gate, outcome_memory, provenance, setup_record

    tj.append(TID, "created", name=WORDS)
    tj.index_put(TID, WORDS, "running", time.time())
    # decision and outcome (the decision BOM), the outcome's journal receipt
    tj.push_task(TID)
    try:
        ref = G.record_external("probe:a12_send", surface="test", target=WORDS, task_id=TID)
        G.close_external(ref, True, detail="probe")
    finally:
        tj.pop_task()
    aid = ref["action_id"]
    decision = next(e for e in L.entries(["decision"]) if e["rec"].get("action_id") == aid)
    # the goal check the task ran, and the goal's receipt
    tj.verification(TID, "goal:%s:m1" % GID, "passed", basis="probe")
    goals._append_receipt(GID, goals._make_receipt(
        {"goal_id": GID, "title": WORDS}, {"milestone_id": "m1", "name": WORDS},
        kind="milestone_complete", verdict={"passed": True, "score": 1.0, "critique": WORDS},
        artifact_text=WORDS, **{"cost_mψ": 0}))
    # the send, in the egress ledger
    egress_gate.record_service_send("mail", "probe", destination=WORDS.lower() + ".invalid",
                                    content=None, action_id=aid)
    # a change to the owner's things
    action_journal.record("organize_files", "move", "files", items=[{"path": str(tmp_path / WORDS)}],
                          summary=WORDS, status="complete")
    # activity rows: a tool call, a reasoning trace pointer, a subagent
    activity_ledger.record("tool_call", tool="probe_a12", ok=True, duration_ms=1, task_id=TID)
    activity_ledger.record("reasoning_trace", trace_id="tr_a12_probe", task_id=TID, model="probe")
    activity_ledger.record("subagent_spawn", task_id=TID, description=WORDS)
    # the other action ledger kinds, as their modules write them
    L.append("web_site", {"event": "trusted", "host": WORDS.lower() + ".invalid"})
    L.append("inbox_cleanup", {"event": "archived", "sender": WORDS.lower() + "@example.invalid"})
    L.append("send_grant", {"event": "granted", "recipient": WORDS.lower() + "@example.invalid"})
    L.append("dissent", {"event": "pushed_back", "reason": WORDS})
    L.append("item_batch_run", {"run_id": "brun_a12_probe", "state": "started", "domain": "email",
                                "count": 1, "decision_ref": {"action_id": aid}})
    L.append("setting_changed", {"setting": "probe", "before": WORDS, "after": 1})
    outcome_memory._receipt("action:%s" % aid, ["om_a12_probe"])
    # a file Friday made, its manifest line naming the decision and the task
    made = tmp_path / ("%s.md" % WORDS)
    made.write_bytes(WORDS.encode())
    sha = hashlib.sha256(WORDS.encode()).hexdigest()
    line = am._sign({"kind": "artifact", "ts": "2026-10-30T10:00:00Z", "path": str(made.resolve()),
                     "sha256": sha, "bytes": len(WORDS), "tool": "write_file", "task_id": TID,
                     "turn_id": None, "args_hash": None,
                     "receipt": {"hmac": decision["rec"].get("hmac"), "decision": "allow"},
                     "provenance": False, "qa": {"checked": True, "ok": True, "findings": [WORDS]}})
    with open(am.manifest_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    # its content credential
    provenance.store_manifest({"artifact": {"content_hash": "sha256:" + sha}, "media_type": "text/markdown",
                               "creator": {"user": WORDS}, "license": {"terms": "CC0"}})
    # a post's publish attempt
    content_pipeline.append_publish_log({"event": "publish", "outcome": "confirmed",
                                         "target_id": "tgt_a12_probe", "post_id": "post_a12_probe",
                                         "url": "https://%s.invalid/p" % WORDS.lower(), "ok": True,
                                         "error": None, "action_id": aid})
    # a setup choice, a memory write, a skill change
    setup_record.append("probe_step", WORDS, "you", city=WORDS)
    CognitiveMemory(memory_dir=tmp_path / "memory").write_memory("a12-" + WORDS, WORDS)
    folder = tmp_path / "skills" / ("a12-" + WORDS)
    folder.mkdir(parents=True)
    skill_registry._append_ledger(folder, "create", reason=WORDS, actor="owner")
    return {"action_id": aid, "decision_ref": decision["rec"].get("hmac", "")[:24]}


def _dump(out) -> str:
    return json.dumps(out, sort_keys=True)


# ── 1. every family, one shape, no content ───────────────────────────────────

def test_every_registered_family_appears_in_one_shape_and_says_nothing_a_record_says(tmp_path):
    _seed(tmp_path)
    out = U.unified(limit=U.PAGE_MAX)
    got = {r["family"] for r in out["rows"]}
    assert got == set(U.FAMILIES), sorted(set(U.FAMILIES) - got)
    assert all(s["ok"] for s in out["sources"].values()), out["sources"]
    assert {n for n, _f, _r in U.SOURCES} <= set(out["sources"])
    for r in out["rows"]:
        assert set(r) == ROW_KEYS, r
        assert isinstance(r["signed"], bool) and isinstance(r["chained"], bool)
        assert isinstance(r["at"], float) and r["summary"] and r["where"]
        assert set(r["ids"]) <= set(U.ID_KEYS)
        assert all(ID_SHAPE.fullmatch(v) for v in r["ids"].values()), r["ids"]
    text = _dump(out).lower()
    assert WORDS.lower() not in text, "no content, path, address or name reaches the list"
    assert str(tmp_path).lower() not in text and "invalid" not in text
    by = {r["family"]: r for r in out["rows"]}
    assert by["decision"]["signed"] and by["decision"]["chained"]
    assert by["egress"]["kind"] == "mail" and not by["egress"]["signed"]
    assert by["artifact"]["signed"] and by["provenance"]["chained"]
    assert by["setup"]["chained"] and by["memory"]["chained"]
    seqs = [r["at"] for r in out["rows"]]
    assert seqs == sorted(seqs, reverse=True), "newest first"


# ── 2. a failing source ──────────────────────────────────────────────────────

def test_a_failing_source_is_reported_and_the_others_are_still_listed(tmp_path, monkeypatch):
    _seed(tmp_path)

    def broken(ctx):
        raise OSError("cannot open %s" % (tmp_path / WORDS))
    monkeypatch.setattr(U, "SOURCES", [(n, f, broken if n == "egress" else r) for n, f, r in U.SOURCES])
    out = U.unified(limit=U.PAGE_MAX)
    assert out["sources"]["egress"]["ok"] is False and out["sources"]["egress"]["error"] == "OSError"
    assert all(s["ok"] for n, s in out["sources"].items() if n != "egress")
    fams = {r["family"] for r in out["rows"]}
    assert "egress" not in fams and {"decision", "goal", "journal", "artifact"} <= fams
    assert WORDS not in _dump(out) and str(tmp_path) not in _dump(out), "the error's type only"


def test_a_read_out_of_time_says_so_and_never_raises(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(U, "TOTAL_BUDGET_S", 0.0)
    out = U.unified()
    assert out["rows"] == []
    assert out["sources"] and all(s["error"] == "out_of_time" for s in out["sources"].values())


# ── 3. filter by family ──────────────────────────────────────────────────────

def test_filter_by_family(tmp_path):
    _seed(tmp_path)
    goal = U.unified(family="goal")["rows"]
    assert goal and {r["family"] for r in goal} == {"goal"}
    assert goal[0]["ids"]["goal_id"] == GID and goal[0]["summary"] == "Goal receipt: milestone complete"
    two = U.unified(family="decision,outcome")
    assert {r["family"] for r in two["rows"]} == {"decision", "outcome"}
    assert two["sources"]["egress"].get("skipped") == "family", "a store with none of them is not read"
    assert U.unified(family="journal")["rows"][0]["source"] == "task_journal"
    assert U.unified(family="no_such_family")["rows"] == []


# ── 4. trace an action ───────────────────────────────────────────────────────

def test_an_action_id_traces_its_decision_outcome_journal_send_and_goal_receipt(tmp_path):
    ids = _seed(tmp_path)
    aid = ids["action_id"]
    other = G.record_external("probe:a12_other", surface="test", target="probe")   # unrelated
    G.close_external(other, True)
    out = U.unified(id=aid, limit=U.PAGE_MAX)
    assert out["traced"] is True
    rows = out["rows"]
    links = {(r["family"], r["kind"], r["link"]) for r in rows}
    assert ("decision", "decision", "direct") in links
    assert ("outcome", "outcome", "direct") in links
    assert ("journal", "receipt", "direct") in links
    assert ("egress", "mail", "direct") in links
    assert ("forget", "outcome_memory_forget", "direct") in links
    assert ("publish", "publish", "direct") in links
    assert ("journal", "verification", "via_task") in links
    assert ("tool", "tool_call", "via_task") in links
    assert ("trace", "reasoning_trace", "via_task") in links
    assert ("artifact", "artifact", "via_task") in links
    assert ("goal", "goal", "via_goal") in links
    receipt = next(r for r in rows if r["family"] == "journal" and r["kind"] == "receipt")
    assert receipt["ids"]["action_id"] == aid and receipt["ids"]["task_id"] == TID
    assert all(r["ids"].get("action_id") in (None, aid) for r in rows)
    assert other["action_id"] not in _dump(out), "an unrelated action is not pulled in"
    assert WORDS not in _dump(out)


def test_a_goal_id_finds_its_receipt_and_the_task_that_was_checked_for_it(tmp_path):
    ids = _seed(tmp_path)
    rows = U.unified(id=GID, limit=U.PAGE_MAX)["rows"]
    links = {(r["family"], r["link"]) for r in rows}
    assert ("goal", "direct") in links and ("journal", "direct") in links
    assert ("decision", "via_task") in links
    assert any(r["ids"].get("action_id") == ids["action_id"] for r in rows)


def test_an_id_that_is_not_an_id_finds_nothing_and_reads_no_store(tmp_path):
    _seed(tmp_path)
    out = U.unified(id="..\\..\\" + WORDS)
    assert out["rows"] == [] and out["sources"] == {}


# ── 5. paging ────────────────────────────────────────────────────────────────

def _tool_rows(n: int) -> None:
    from agent_friday.services import activity_ledger
    for i in range(n):
        activity_ledger.record("tool_call", tool="probe_page_%d" % i, ok=True, duration_ms=i)


def test_paging_is_stable_while_newer_records_arrive(tmp_path):
    _tool_rows(12)
    first = U.unified(family="tool", limit=5)
    assert first["count"] == 5 and first["next_cursor"]
    second = U.unified(family="tool", limit=5, cursor=first["next_cursor"])
    _tool_rows(3)                                     # newer records arrive
    again = U.unified(family="tool", limit=5, cursor=first["next_cursor"])
    assert [r["ref"] for r in again["rows"]] == [r["ref"] for r in second["rows"]]
    third = U.unified(family="tool", limit=5, cursor=second["next_cursor"])
    refs = [r["ref"] for p in (first, second, third) for r in p["rows"]]
    assert len(refs) == 12 and len(set(refs)) == 12, "every record once, none twice"
    assert third["next_cursor"] is None
    keys = [U._sort_key(r) for p in (first, second, third) for r in p["rows"]]
    assert keys == sorted(keys, reverse=True)


def test_a_cursor_this_list_did_not_give_out_is_refused():
    with pytest.raises(U.BadCursor):
        U.unified(cursor="not-a-cursor!")


# ── 6. the Ledger page ───────────────────────────────────────────────────────

def test_the_ledger_page_shows_everything_together_with_textcontent_only():
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    assert "Everything, together" in html and 'id="u-family"' in html and 'id="u-id"' in html
    start = html.index("function unifiedRender")
    body = html[start:html.index("$('u-more').addEventListener", start)]
    assert "/api/ledger/unified" in body and "textContent" in body and "el(" in body
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in html, sink
