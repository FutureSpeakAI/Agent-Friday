"""Five small places where what Friday shows did not match what she does.

* The weekly editorial said "first draft was too safe, so I rewrote it with
  more pushback" (and the card said "rewritten for stronger pushback") when the
  only retry left is for an EMPTY first draft.
* Messages and setup copy sent people to a "Task Tray" that no longer exists;
  a task's controls are on its task card in System.
* The Finance contacts tab said "Add in ~/.friday/wiki/finance/", a folder it
  never reads.
* _load_vault_summary still read the epistemic score file, which no prompt
  reads.
* A task finished by the resume path was saved as 'completed', a status the
  task card has no word (and no View Results) for; the card's word for a
  finished task is COMPLETE, status 'complete'.
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import time
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = [ROOT / "index.html", ROOT / "ui_parts" / "app.html"]
PAGE_IDS = ["index", "app"]


def _page(p):
    return p.read_text(encoding="utf-8")


# ── (6) the editorial says only what happened ───────────────────────────────

class TestEditorialRetryWording:
    def test_the_stored_note_says_the_first_attempt_was_empty(self):
        from agent_friday.services import news_engine as ne
        md = ne._editorial_markdown("2026-W01", "2026-01-02 19:00 CST", "Body text.",
                                    [], 0.5, True)
        assert "too safe" not in md and "pushback" not in md
        assert "came back empty" in md

    def test_an_empty_first_draft_is_retried_and_the_note_says_so(self, monkeypatch, tmp_path):
        from agent_friday.services import news_engine as ne
        replies = iter(["", "A second attempt with words in it."])
        monkeypatch.setattr(ne, "_generate_text", lambda messages, **k: next(replies))
        monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **k: "sys", raising=False)
        monkeypatch.setattr(ne, "_predict_route_provider", lambda **k: "local", raising=False)
        monkeypatch.setattr(ne, "_gated_vault_control", lambda: None, raising=False)
        monkeypatch.setattr(ne, "_gather_editorial_pool",
                            lambda days=7: [{"source": "a.test", "title": "Story", "snippet": "x"}])
        monkeypatch.setattr(ne, "_load_banned_sources", lambda: [], raising=False)
        monkeypatch.setattr(ne, "EDITORIALS_DIR", tmp_path)
        monkeypatch.setattr(ne, "_editorial_independence_score", lambda text: 0.5)
        monkeypatch.setattr(ne, "_notify_weekly_editorial", lambda *a, **k: None, raising=False)
        out = ne._generate_weekly_editorial()
        assert out.get("regenerated") is True
        assert "pushback" not in out["markdown"] and "too safe" not in out["markdown"]
        assert "came back empty" in out["markdown"]

    @pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
    def test_the_editorial_card_does_not_claim_a_rewrite_for_pushback(self, page):
        text = _page(page)
        assert "stronger pushback" not in text
        assert "first attempt came back empty" in text


# ── (7) no copy points at the Task Tray ─────────────────────────────────────

_TRAY = re.compile(r"(?i)\btask tray\b")
# Python 3.12+ tokenizes the text of an f-string as FSTRING_MIDDLE.
_STRING_TOKENS = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def _python_strings_naming_the_tray():
    hits = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in _STRING_TOKENS and _TRAY.search(tok.string):
                hits.append("%s:%d" % (path.relative_to(ROOT).as_posix(), tok.start[0]))
    return hits


def test_no_python_string_sends_anyone_to_the_task_tray():
    """Messages, notices and tool descriptions (which the model repeats to the
    owner) name the task card in System instead."""
    assert _python_strings_naming_the_tray() == []


INSTRUCTIONS = ROOT / "src" / "agent_friday" / "resources" / "instructions"


def test_no_instruction_file_sends_anyone_to_the_task_tray():
    """Friday's core instructions live in Markdown files now, not a Python
    string; the model repeats the reply they script ('Started - ...') to the
    owner, so they name the task card in System too."""
    files = sorted(INSTRUCTIONS.rglob("*.md"))
    assert files, "no instruction files found under %s" % INSTRUCTIONS
    hits = ["%s:%d" % (f.relative_to(ROOT).as_posix(), i)
            for f in files
            for i, line in enumerate(f.read_bytes().decode("utf-8").splitlines(), 1)
            if _TRAY.search(line)]
    assert hits == [], "instruction lines naming the task tray: %s" % hits
    delegation = (INSTRUCTIONS / "85_task_delegation.md").read_bytes().decode("utf-8")
    assert "follow it on its task card in System" in delegation


def test_the_shipped_core_prompt_does_not_name_the_task_tray():
    from agent_friday.services import instruction_files as inf
    core = inf.shipped_core()
    assert not _TRAY.search(core)
    assert "follow it on its task card in System" in core


@pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
def test_the_page_names_the_task_tray_only_in_comments(page):
    shown = [i for i, line in enumerate(_page(page).splitlines(), 1)
             if _TRAY.search(line)
             and not line.lstrip().startswith(("//", "/*", "*"))]
    assert shown == [], "lines that show 'Task Tray' to the owner: %s" % shown


@pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
def test_the_setup_research_copy_names_the_task_card(page):
    text = _page(page)
    assert "watch it in the tray" not in text
    assert "Running as a background task: see the tray" not in text
    assert "you can watch it on its task card in System" in text
    assert "Running as a background task: see its task card in System" in text
    assert "Open this task's card in System and choose View Results" in text


# ── (8) the Finance contacts tab names no folder it does not read ───────────

@pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
def test_the_contacts_tab_does_not_name_a_folder_it_never_reads(page):
    text = _page(page)
    assert "wiki/finance" not in text
    assert text.count("Placeholder: this tab does not read any file yet.") == 2


# ── (9) the vault summary does not load the epistemic score ─────────────────

def test_the_vault_summary_does_not_read_the_epistemic_store(monkeypatch, tmp_path):
    from agent_friday.services import model_router as mr
    (tmp_path / "personality.json").write_text(
        json.dumps({"maturity": 0.4, "session_count": 3, "traits": {"warmth": 0.8}}),
        encoding="utf-8")
    for name in ("epistemic_scores.json", "epistemic.json"):
        (tmp_path / name).write_text(json.dumps({"overall_score": 0.31}), encoding="utf-8")
    monkeypatch.setattr(mr, "FRIDAY_DIR", tmp_path)
    reads = []
    real_read_text = pathlib.Path.read_text

    def _recording_read_text(self, *a, **k):
        reads.append(self.name)
        return real_read_text(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", _recording_read_text)
    ctx = mr._load_vault_summary()
    assert "epistemic" not in ctx
    assert not [n for n in reads if n.startswith("epistemic")], reads
    assert ctx["personality"]["session_count"] == 3, "the rest of the summary still loads"


# ── (11) a resumed task that finishes shows COMPLETE ─────────────────────────

@pytest.fixture
def task_client(monkeypatch):
    import agent_friday.core as core
    from agent_friday.routes import tasks as rt
    from agent_friday.services import agent
    if rt.tasks_bp.name not in core.app.blueprints:
        core.app.register_blueprint(rt.tasks_bp)
    core.app.config["TESTING"] = True

    def _fake_task_set(task_id, **fields):
        with agent.TASKS_LOCK:
            if task_id in agent.TASKS:
                agent.TASKS[task_id].update(fields)
    monkeypatch.setattr(agent, "_task_set", _fake_task_set)
    saved = {}
    with agent.TASKS_LOCK:
        saved.update(agent.TASKS)
        agent.TASKS.clear()
    yield core.app.test_client(), agent
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
        agent.TASKS.update(saved)


def _record(agent, tid, status):
    now = time.time()
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": "Synthetic resumable task",
                            "description": "", "prompt": "summarise the sample notes",
                            "status": status, "created": now - 60, "started": now - 60,
                            "ended": now - 1 if status != "interrupted" else None,
                            "log": [], "result": "", "step_n": 0}


def test_a_task_the_resume_path_finishes_is_saved_complete(task_client, monkeypatch):
    client, agent = task_client
    from agent_friday.services import task_resume as tr
    _record(agent, "resume-word-0001", "interrupted")
    monkeypatch.setattr(tr, "resumability", lambda tid: {
        "resumable": True, "needs_confirmation": False, "reason": "it will continue",
        "iteration": 2})
    monkeypatch.setattr(tr, "resume", lambda tid, **kw: ("The notes are summarised.", []))
    r = client.post("/api/tasks/resume-word-0001/resume", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    for _ in range(300):
        with agent.TASKS_LOCK:
            st = agent.TASKS["resume-word-0001"]["status"]
        if st != "running":
            break
        time.sleep(0.01)
    assert st == "complete", "the card has a status word for 'complete' only"
    body = client.get("/api/tasks/resume-word-0001").get_json()
    assert body["status"] == "complete"


def test_a_record_already_saved_completed_reads_complete(task_client):
    client, agent = task_client
    _record(agent, "resume-word-0002", "completed")
    one = client.get("/api/tasks/resume-word-0002").get_json()
    assert one["status"] == "complete"
    rows = client.get("/api/tasks").get_json()["tasks"]
    row = next(t for t in rows if t.get("task_id") == "resume-word-0002")
    assert row["status"] == "complete"


@pytest.mark.parametrize("page", PAGES, ids=PAGE_IDS)
def test_the_task_card_word_for_complete_is_complete(page):
    text = _page(page)
    assert re.search(r"status\s*===\s*'complete'\s*&&", text), "the card keys COMPLETE on 'complete'"
    assert "COMPLETE" in text
