"""Deep research as a tool, durable across a restart, with sealed sources.

  * `deep_research` is a registered, classified tool that starts a background
    task and returns its id at once;
  * every page a finding cites is sealed as a protected snapshot (text, URL,
    fetch time, SHA-256) so its quotes can be checked later;
  * a run killed mid-grind resumes after the restart: completed sub-questions
    are not redone, the interrupted one is redone without duplicate findings,
    and a commission bound to a task resumes on that same task.

No network and no models: search, fetch and the local model calls are faked.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import research, web_fetch, web_search
from agent_friday.services.research import harness, snapshots
from agent_friday.services.research.objects import (
    Commission, ResearchPlan, SubQuestion,
)

PAGE = ("The harbour bridge opened in 1932. It carries eight lanes of road "
        "and two railway tracks across the harbour.")


class _Crash(BaseException):
    """A process death: not an Exception, so nothing in the run catches it."""


class FakeWeb:
    """Search, fetch and the three model calls, recorded per sub-question."""

    def __init__(self, crash_on=None):
        self.searches: list[str] = []
        self.fetches: list[str] = []
        self.crash_on = crash_on          # (query_substring) that dies once
        self.crashed = False

    def search(self, query, count=8):
        self.searches.append(query)
        if self.crash_on and self.crash_on in query and not self.crashed:
            self.crashed = True
            raise _Crash("process died mid-grind")
        slug = query.split(":")[0]
        return {"status": web_search.SearchStatus.OK, "backend": "fake",
                "results": [{"url": f"https://example.org/{slug}"}]}

    def fetch(self, url, **kw):
        self.fetches.append(url)
        return web_fetch.FetchResult({
            "ok": True, "id": "src" + hashlib.blake2b(url.encode(), digest_size=6).hexdigest(),
            "url": url, "final_url": url, "title": "Bridge facts",
            "fetched_at": 1_700_000_000.0, "chars": len(PAGE)})

    def load_extraction(self, source_id):
        return PAGE

    def json_local(self, system, user, model, **kw):
        if system == harness._QUERY_SYSTEM:
            # "Sub-question: sqN text" -> one query tagged with the sub-question.
            line = user.splitlines()[0].replace("Sub-question: ", "")
            return {"queries": [line]}
        if system == harness._EXTRACT_SYSTEM:
            return {"passages": ["The harbour bridge opened in 1932."]}
        if system == harness._CONVERSE_SYSTEM:
            sq = user.splitlines()[0].replace("Sub-question: ", "")
            src = user.split("CORPUS:\n[")[1].split("]")[0]
            # sq1 asks for one follow-up, so its second search can be the one
            # the crash lands on, AFTER its first finding is on disk.
            depth = user.count("\n[src")      # passages in the corpus so far
            follow = "sq1:more" if sq.startswith("sq1") and depth == 1 else ""
            return {"answer": "1932", "findings": [
                {"claim": f"Opened in 1932 ({sq})",
                 "quote": "The harbour bridge opened in 1932.", "source_id": src}],
                "gaps": [], "best_followup": follow, "done": not follow}
        return None


@pytest.fixture
def web(monkeypatch):
    fw = FakeWeb()
    monkeypatch.setattr(web_search, "search", fw.search)
    monkeypatch.setattr(web_search, "canary", lambda force=False: {"ok": True})
    monkeypatch.setattr(web_fetch, "fetch", fw.fetch)
    monkeypatch.setattr(web_fetch, "load_extraction", fw.load_extraction)
    monkeypatch.setattr(harness, "_json_local", fw.json_local)
    return fw


def _commission(n_sq=3) -> Commission:
    c = Commission("When did the harbour bridge open?",
                   commission_id="t" + uuid.uuid4().hex[:11])
    c.plan = ResearchPlan(commission_id=c.id, scoped_by="given", sub_questions=[
        SubQuestion(id=f"sq{i}", text=f"sq{i}: bridge fact {i}") for i in range(n_sq)])
    c.save()
    return c


# ── the tool ────────────────────────────────────────────────────────────────

def test_deep_research_is_a_registered_internal_network_tool():
    assert "deep_research" in agent.CLAUDE_TOOL_HANDLERS
    assert any(t["name"] == "deep_research" for t in agent.CLAUDE_TOOLS)
    assert action_gate.known("deep_research")
    klass, _ = action_gate.classify("deep_research", {"question": "x"})
    assert klass == action_gate.INTERNAL
    assert agent.TOOL_RINGS["deep_research"] == 2


def test_the_tool_starts_a_task_and_returns_its_id(monkeypatch):
    from agent_friday.services import judgment_gate as jg
    monkeypatch.setattr(jg, "dry_run", lambda text: {
        "cloud_allowed": True, "question_sent": text, "scrub_tags": [],
        "scrub_count": 0, "reason": ""})
    ran = []

    def fake_run(cid):
        ran.append(cid)
        c = Commission.load(cid)
        return {**c.status_dict(), "status": "delivered", "findings": 2,
                "styled_path": "report.html"}
    monkeypatch.setattr(research, "run", fake_run)

    out = json.loads(agent._tool_deep_research({
        "question": "When did the harbour bridge open?",
        "sub_questions": ["opening date", "who built it"], "max_sources": 5}))
    tid, cid = out["task_id"], out["commission_id"]
    agent.TASK_THREADS[tid].join(10)

    assert ran == [cid]
    t = agent.TASKS[tid]
    assert t["status"] == "complete", t
    assert t["research_commission_id"] == cid
    c = Commission.load(cid)
    assert c.task_id == tid, "the commission is bound to its task for resume"
    assert c.budget["fetches_total"] == 5 and c.budget["fetches_per_sq"] == 5
    assert [s.text for s in c.plan.sub_questions] == ["opening date", "who built it"]


def test_the_tool_refuses_without_a_question():
    assert "required" in agent._tool_deep_research({})


def test_a_failed_commission_is_a_failed_task(monkeypatch):
    from agent_friday.services import judgment_gate as jg
    monkeypatch.setattr(jg, "dry_run", lambda text: {"cloud_allowed": True})
    monkeypatch.setattr(research, "run", lambda cid: {
        "status": "failed", "failure": "My search tool is broken."})
    out = json.loads(agent._tool_deep_research({"question": "q"}))
    agent.TASK_THREADS[out["task_id"]].join(10)
    t = agent.TASKS[out["task_id"]]
    assert t["status"] == "failed" and "search tool is broken" in t["result"]


# ── snapshots ───────────────────────────────────────────────────────────────

def test_every_cited_page_is_sealed_with_url_time_and_hash(web):
    c = _commission(n_sq=1)
    harness.grind(c)
    c = Commission.load(c.id)
    f = c.findings()[0]

    snaps = snapshots.list_snapshots(c.id)
    assert [s["source_id"] for s in snaps] == [f.source_id]
    s = snaps[0]
    assert s["url"] == "https://example.org/sq0"
    assert s["fetched_at"] == 1_700_000_000.0
    assert s["sha256"] == hashlib.sha256(PAGE.encode()).hexdigest()
    assert s["intact"] is True
    assert snapshots.quote_holds(c.id, f.source_id, f.quote)
    assert not snapshots.quote_holds(c.id, f.source_id, "The bridge opened in 1999.")

    raw = (c.dir / "snapshots" / f"{f.source_id}.snap").read_bytes()
    from agent_friday.services import credential_store as cs
    if cs.protection_method() != "plaintext":
        assert b"harbour bridge" not in raw, "the page text is stored in the clear"


def test_a_snapshot_whose_text_changed_is_not_intact(web, monkeypatch):
    c = _commission(n_sq=1)
    rec = snapshots.snapshot(c.id, "srcabc", url="https://example.org/x", text=PAGE)
    assert rec and rec["sha256"] == hashlib.sha256(PAGE.encode()).hexdigest()
    # Simulate an edited record: decrypt, change the text, re-seal.
    p = c.dir / "snapshots" / "srcabc.snap"
    body = json.loads(snapshots._unprotect(p.read_bytes()))
    body["text"] = PAGE.replace("1932", "1999")
    p.write_bytes(snapshots._protect(json.dumps(body).encode())[0])
    assert snapshots.load(c.id, "srcabc")["intact"] is False
    assert not snapshots.quote_holds(c.id, "srcabc", "opened in 1999")


def test_verification_falls_back_to_the_sealed_snapshot(web, monkeypatch):
    c = _commission(n_sq=1)
    harness.grind(c)
    c = Commission.load(c.id)
    fid = c.findings()[0].id
    monkeypatch.setattr(web_fetch, "load_extraction", lambda sid: "")  # cache cleared
    out = harness.verify(c, {"sections": [{"heading": "h", "body": f"x [F:{fid}]"}]})
    assert out["verified_ids"] == [fid]


# ── resume after a restart ──────────────────────────────────────────────────

def test_a_grind_resumes_after_a_crash_without_redoing_completed_steps(web):
    c = _commission(n_sq=3)
    web.crash_on = "sq1:more"             # dies in sq1, after its first finding
    with pytest.raises(_Crash):
        harness.grind(c)

    before = Commission.load(c.id)
    assert before.step_done("sq:sq0") and not before.step_done("sq:sq1")
    assert any(f.sub_question_id == "sq1" for f in before.findings()), \
        "the crash should leave a partial finding behind for this test to mean anything"

    # "Restart": a fresh load of the commission from disk, a fresh grind.
    web.searches.clear()
    after = Commission.load(c.id)
    harness.grind(after)

    assert not any(q.startswith("sq0") for q in web.searches), \
        f"a completed sub-question was searched again: {web.searches}"
    assert any(q.startswith("sq1") for q in web.searches)
    assert any(q.startswith("sq2") for q in web.searches)
    final = Commission.load(c.id)
    assert all(final.step_done(f"sq:sq{i}") for i in range(3))
    per_sq = {}
    for f in final.findings():
        per_sq[f.sub_question_id] = per_sq.get(f.sub_question_id, 0) + 1
    # sq1 yields two findings (first pass + follow-up); nothing is doubled.
    assert per_sq == {"sq0": 1, "sq1": 2, "sq2": 1}, per_sq
    assert final.progress["fetches"] == 4


def test_run_does_not_scope_again_when_the_plan_is_recorded(monkeypatch):
    c = _commission(n_sq=2)
    monkeypatch.setattr(harness, "refresh_seats", lambda: {})
    monkeypatch.setattr(harness, "scope", lambda c: pytest.fail("scoped a planned commission"))
    grinds = []
    monkeypatch.setattr(harness, "grind", lambda c: grinds.append(c.id))
    monkeypatch.setattr(harness, "synthesize", lambda c: None)
    from agent_friday.services.research import deliver
    monkeypatch.setattr(deliver, "announce_failure", lambda c: None)
    research.run(c.id)
    assert grinds == [c.id]
    assert Commission.load(c.id).step_done("scope")


def test_a_finished_commission_is_never_run_twice(monkeypatch):
    c = _commission(n_sq=1)
    c.status = "delivered"
    c.save()
    monkeypatch.setattr(harness, "refresh_seats", lambda: {})
    monkeypatch.setattr(harness, "grind", lambda c: pytest.fail("re-ran a delivered commission"))
    assert research.run(c.id)["status"] == "delivered"


def test_reconcile_resumes_a_research_task_on_its_own_record(monkeypatch):
    from agent_friday.services import reconcile
    c = _commission(n_sq=1)
    tid = "task-" + uuid.uuid4().hex[:8]
    c.task_id = tid
    c.status = "grinding"
    c.progress["stage"] = "grinding"
    c.save()
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "id": tid, "name": "Research: bridge",
                            "status": "interrupted", "created": 1.0, "log": []}
    ran = []

    def fake_run(cid):
        ran.append(cid)
        return {"status": "delivered", "findings": 1, "report_path": "r.md"}
    monkeypatch.setattr(research, "run", fake_run)
    monkeypatch.setattr(reconcile, "_commissions", lambda: [Commission.load(c.id)])
    agent.TASKS_RESTORED.set()

    out = reconcile.reconcile_research(resume=True)
    assert c.id in out["resumed"]
    for th in [t for t in __import__("threading").enumerate()
               if t.name in (f"resume-research-{c.id}", f"task-{tid[:8]}")]:
        th.join(10)
    th = agent.TASK_THREADS.get(tid)
    if th:
        th.join(10)
    assert ran == [c.id], "the commission should run exactly once, on its task"
    assert agent.TASKS[tid]["status"] == "complete"
    assert "resumed" in agent.TASKS[tid].get("status_reason", "")


def test_a_synthesizing_commission_is_adopted_after_a_restart():
    from agent_friday.services import reconcile
    assert "synthesizing" in reconcile.RUNNING_STAGES
