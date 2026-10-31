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
  * a goal id finds its receipt and its check rows and stops there (the
    checked task's other events are not pulled in);
  * paging by cursor is stable while newer records arrive;
  * the Ledger page's "Everything, together" section sets text with
    textContent only, and "Older records" pages the list it was given;
  * bounded (review of 3ac5a4913): the action ledger read stops on its
    deadline, a trace starts no pass once the total is spent, encrypted task
    journals are read within a line cap, from their end, with the time
    checked per line; `truncated` names the task cap and the tail limit;
    one read at a time (Busy) and the plain first page is kept CACHE_S;
  * two publish events in the same second page as two records; signatures
    are shown only as keyed hashes and an artifact still traces by the id
    shown or by the task journal's reference.
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
    U.reset_cache()
    yield
    U.reset_cache()
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
    return {"action_id": aid, "decision_sig": decision["rec"].get("hmac") or "", "manifest_line": line}


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


def test_a_goal_id_finds_its_receipt_and_its_check_rows_and_stops_there(tmp_path):
    ids = _seed(tmp_path)
    rows = U.unified(id=GID, limit=U.PAGE_MAX)["rows"]
    links = {(r["family"], r["link"]) for r in rows}
    assert ("goal", "direct") in links and ("journal", "direct") in links
    check = next(r for r in rows if r["family"] == "journal" and r["kind"] == "verification")
    assert check["ids"]["task_id"] == TID and check["ids"]["goal_id"] == GID
    # goal -> its check rows -> stop: the checked task's own events are not pulled in
    assert not any(r["link"] == "via_task" for r in rows), links
    assert not any(r["ids"].get("action_id") == ids["action_id"] for r in rows)


def test_a_task_named_for_itself_is_still_followed_when_its_row_also_names_a_goal(tmp_path):
    ids = _seed(tmp_path)
    # tracing the action: the task comes from its receipt, not a goal check
    rows = U.unified(id=ids["action_id"], limit=U.PAGE_MAX)["rows"]
    assert any(r["family"] == "journal" and r["kind"] == "verification" and r["link"] == "via_task"
               for r in rows)


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


def test_older_records_pages_the_list_it_was_given_not_the_form_as_it_is_now():
    html = (ROOT / "static" / "ledger.html").read_text(encoding="utf-8")
    start = html.index("function unifiedLoad")
    body = html[start:html.index("$('u-more').addEventListener", start)]
    # the form is read only for a first page; "Older records" reuses that query
    assert "if (!append) unifiedQuery = { family: $('u-family').value, id: $('u-id').value.trim() };" in body
    assert "const fam = unifiedQuery.family, id = unifiedQuery.id;" in body
    assert body.count("$('u-id').value") == 1 and body.count("$('u-family').value") == 1


# ── 7. bounded: deadlines, caps, one read at a time ──────────────────────────

class _Clock:
    """A clock that moves `step` seconds each time it is read."""

    def __init__(self, step: float = 0.0):
        self.t = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_the_action_ledger_read_stops_on_its_deadline_not_only_at_its_limit(monkeypatch):
    for i in range(400):
        L.append("dissent", {"event": "pushed_back", "n": i})
    seen = [0]
    real = U._ledger_row

    def counting(e):
        seen[0] += 1
        return real(e)
    monkeypatch.setattr(U, "_ledger_row", counting)
    monkeypatch.setattr(U, "_clock", _Clock(step=0.02))      # 2 s after about 100 lines
    # an id nothing names: before, every line of the ledger was read for it
    out = U.unified(id="act_a12_nothing_names_this", limit=U.PAGE_MAX)
    rep = out["sources"]["action_ledger"]
    assert rep["out_of_time"] is True, rep
    assert 0 < seen[0] < 200, seen[0]
    assert out["out_of_time"] is True and out["rows"] == []


def test_a_trace_starts_no_further_pass_once_the_total_time_is_spent(tmp_path, monkeypatch):
    ids = _seed(tmp_path)
    clock = _Clock()
    monkeypatch.setattr(U, "_clock", clock)
    real = U._read_sources

    def then_late(**kw):
        rows = real(**kw)
        clock.t += U.TOTAL_BUDGET_S + 1.0           # the direct pass used it all
        return rows
    monkeypatch.setattr(U, "_read_sources", then_late)
    out = U.unified(id=ids["action_id"], limit=U.PAGE_MAX)
    assert out["rows"] and {r["link"] for r in out["rows"]} == {"direct"}
    assert "via_task" in out["skipped_passes"] and out["out_of_time"] is True
    assert out["cached"] is False


def _encrypted(monkeypatch):
    from agent_friday.privacy import vault_crypto as vc
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(tj, "settings", lambda: {"retention_days": 0, "capture_reasoning": True,
                                                 "encrypt_at_rest": True})
    derived = vc.derive_key("synthetic phrase for the a12 journal", b"synthetic-salt-for-a12")
    monkeypatch.setattr(cs, "_vault_key", lambda: derived)


def test_encrypted_task_journals_are_read_within_a_line_cap_and_from_their_end(monkeypatch):
    _encrypted(monkeypatch)
    tid = "a12-encrypted-0001"
    tj.append(tid, "created", name=WORDS)
    for _ in range(40):
        tj.blocker(tid, "run_failed")                 # seq 2 .. 41
    path = tj.task_dir(tid) / "journal.jsonl"
    assert all(ln.startswith(tj._LINE_PREFIX)
               for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    decoded = [0]
    real = tj._decode_line

    def counting(line):
        decoded[0] += 1
        return real(line)
    monkeypatch.setattr(tj, "_decode_line", counting)
    # a line cap: no more lines are decrypted than it allows
    monkeypatch.setattr(U, "PER_TASK_LINES", 10)
    out = U.unified(family="journal", limit=U.PAGE_MAX)
    assert decoded[0] <= 10, decoded[0]
    assert 0 < out["count"] <= 10
    assert "task_journal" in out["truncated"]
    assert out["sources"]["task_journal"]["capped"] is False, "truncated is not capped"
    # a byte cap: the newest events, from the end of the file
    U.reset_cache()
    decoded[0] = 0
    monkeypatch.setattr(U, "PER_TASK_LINES", 1000)
    monkeypatch.setattr(U, "PER_TASK_BYTES", path.stat().st_size // 4)
    out = U.unified(family="journal", limit=U.PAGE_MAX)
    refs = {r["ref"] for r in out["rows"]}

    def ref(seq):
        # a row's ref is "<task>#<seq>" only in its making: "#" is not an id
        # character, so the row carries that ref's digest (see _row)
        return U._journal_row({"kind": "blocker", "task_id": tid, "seq": seq, "ts": 1.0})["ref"]
    assert ref(41) in refs and ref(2) not in refs, sorted(refs)
    assert decoded[0] < 20 and "task_journal" in out["truncated"]
    # and the time is checked per line: a clock that runs out stops the read
    U.reset_cache()
    decoded[0] = 0
    monkeypatch.setattr(U, "PER_TASK_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(U, "_clock", _Clock(step=0.25))       # 2 s after about 8 reads
    out = U.unified(family="journal", limit=U.PAGE_MAX)
    assert decoded[0] < 41 and out["sources"]["task_journal"]["out_of_time"] is True
    assert WORDS not in _dump(out)


def test_truncated_names_the_task_cap_and_the_tail_limit(tmp_path, monkeypatch):
    _seed(tmp_path)
    tj.append("a12-second-task-0002", "created", name=WORDS)
    tj.blocker("a12-second-task-0002", "run_failed")
    out = U.unified(limit=U.PAGE_MAX)
    assert out["truncated"] == [], out["truncated"]
    U.reset_cache()
    monkeypatch.setattr(U, "MAX_TASKS", 1)
    out = U.unified(family="journal", limit=U.PAGE_MAX)
    assert out["truncated"] == ["task_journal"]
    assert out["sources"]["task_journal"]["capped"] is False
    U.reset_cache()
    monkeypatch.setattr(U, "MAX_TASKS", 50)
    monkeypatch.setattr(U, "TAIL_BYTES", 64)                   # less than one egress line
    out = U.unified(family="egress", limit=U.PAGE_MAX)
    assert out["truncated"] == ["egress"] and out["sources"]["egress"]["capped"] is False


def test_concurrent_reads_run_once_and_the_plain_first_page_is_kept(monkeypatch):
    import threading
    calls = [0]
    entered, release = threading.Event(), threading.Event()

    def slow(ctx):
        calls[0] += 1
        entered.set()
        release.wait(5)
        return []
    monkeypatch.setattr(U, "SOURCES", [("egress", ("egress",), slow)])
    monkeypatch.setattr(U, "BUSY_WAIT_S", 10.0)
    results = []
    threads = [threading.Thread(target=lambda: results.append(U.unified())) for _ in range(2)]
    threads[0].start()
    assert entered.wait(5)
    threads[1].start()
    time.sleep(0.2)                                   # the second is waiting on the first
    assert calls[0] == 1
    release.set()
    for t in threads:
        t.join(10)
    assert len(results) == 2 and calls[0] == 1, "the waiting read took the first one's page"
    assert sorted(r["cached"] for r in results) == [False, True]
    assert U.unified()["cached"] is True and calls[0] == 1
    # a trace or a later page is never served from the cache
    assert U.unified(id="act_a12_not_cached")["cached"] is False and calls[0] == 2
    # and the kept page is fresh for CACHE_S only
    clock = _Clock()
    monkeypatch.setattr(U, "_clock", clock)
    U.reset_cache()
    U.unified()
    clock.t += U.CACHE_S + 1.0
    assert U.unified()["cached"] is False and calls[0] == 4


def test_a_read_while_another_holds_the_list_too_long_is_told_busy(monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()

    def slow(ctx):
        entered.set()
        release.wait(5)
        return []
    monkeypatch.setattr(U, "SOURCES", [("egress", ("egress",), slow)])
    monkeypatch.setattr(U, "BUSY_WAIT_S", 0.05)
    first = threading.Thread(target=U.unified)
    first.start()
    try:
        assert entered.wait(5)
        with pytest.raises(U.Busy):
            U.unified(id="act_a12_busy_probe")
    finally:
        release.set()
        first.join(10)


# ── 8. references: same-second publish events, signatures never shown ───────

def test_two_publish_events_in_the_same_second_page_as_two_records(tmp_path):
    from agent_friday.services import content_pipeline
    log = Path(content_pipeline.PUBLISH_LOG)
    log.parent.mkdir(parents=True, exist_ok=True)
    same = "2026-10-30T10:00:00Z"
    with open(log, "w", encoding="utf-8") as f:
        for outcome in ("sent", "confirmed"):
            f.write(json.dumps({"ts": same, "event": "publish", "outcome": outcome,
                                "target_id": "tgt_a12_same"}) + "\n")
    first = U.unified(family="publish", limit=1)
    assert first["count"] == 1 and first["next_cursor"]
    second = U.unified(family="publish", limit=1, cursor=first["next_cursor"])
    assert second["count"] == 1 and second["next_cursor"] is None
    a, b = first["rows"][0], second["rows"][0]
    assert a["at"] == b["at"] and a["ref"] != b["ref"]
    assert {a["summary"], b["summary"]} == {"Post publish attempt: sent", "Post publish attempt: confirmed"}


def test_signatures_are_shown_only_as_keyed_hashes_and_an_artifact_still_traces(tmp_path):
    ids = _seed(tmp_path)
    line = ids["manifest_line"]
    journal_ref = am.artifact_ref(line)
    tj.artifact(TID, journal_ref, tool="write_file", qa_ok=True, action_id=ids["action_id"])
    out = U.unified(limit=U.PAGE_MAX)
    text = _dump(out)
    for sig in (ids["decision_sig"], line["hmac"]):
        assert sig and sig[:16] not in text, "no signature's characters reach the list"
    assert journal_ref[len(am.ARTIFACT_REF_PREFIX):] not in text
    art = next(r for r in out["rows"] if r["family"] == "artifact")
    jart = next(r for r in out["rows"] if r["family"] == "journal" and r["kind"] == "artifact")
    shown = art["ids"]["artifact_id"]
    assert shown.startswith("art_") and shown != journal_ref and jart["ids"]["artifact_id"] == shown
    dec = next(r for r in out["rows"]
               if r["family"] == "decision" and r["ids"].get("action_id") == ids["action_id"])
    assert dec["ids"]["decision_ref"] == art["ids"]["decision_ref"], "the decision and its artifact join"
    # traced by the id shown, or by the reference the task journal keeps (G10)
    for want in (shown, journal_ref):
        rows = U.unified(id=want, limit=U.PAGE_MAX)["rows"]
        got = {(r["family"], r["kind"], r["link"]) for r in rows}
        assert ("artifact", "artifact", "direct") in got and ("journal", "artifact", "direct") in got, got
