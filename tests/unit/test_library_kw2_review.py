"""Review fixes for the KW-2 port: consent is read live for every file, a draft is checked again right before
it is written, a forget leaves nothing staged, and the Library's own holds reach drafting.

Each test fails on the port as first committed (4c156963). Documents are synthetic; the local model is a
function the test hands in.
"""
from __future__ import annotations

import time

import pytest

from library_queue_helpers import install, teardown
from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs

FACT = {"subject": "pier four", "predicate": "has", "object": "a cracked hull",
        "quote": "The harbor inspection found a cracked hull on pier four."}
TEXT = ("The harbor inspection found a cracked hull on pier four.\n"
        "The repair crew will return on the first of March to replace the valve.")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    install(monkeypatch)
    from agent_friday.services.library import staging
    monkeypatch.setattr(staging, "_learning_off", lambda: False)
    yield
    teardown()
    release_library(fg, lstore)


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _folder(tmp_path, n=4, name="Notes"):
    root = tmp_path / name
    write_docs(root, {"n%d.txt" % i: "Meeting notes %d. Agenda and action items about the harbor and the tides, "
                                     "marker zebra%d." % (i, i) for i in range(n)})
    return root


def _doc(st, name, text=TEXT, *, tier=1, shelf="open"):
    fid = st.folder_id("/scope", "")
    did = st.upsert_document("/scope/%s.txt" % name, sha256=name.encode().hex(), size=300, mtime=5.0, kind="text",
                             ext="txt", scope="/scope", folder_id=fid, title=name, shelf=shelf)
    st.set_state(did, "indexed")
    for i, line in enumerate(text.split("\n")):
        st.x("INSERT INTO blocks(doc_id, ord, kind, text) VALUES(?,?,?,?)", (did, i, "paragraph", line))
    st.set_doc_meta(did, doc_type="note", type_score=0.5, sensitivity=tier, dup_of=None, dup_kind=None,
                    dup_score=None, degraded=False, versions={"triage": 1})
    return did


@pytest.fixture
def consent(monkeypatch):
    """The ledger covers every path until a test says otherwise."""
    from agent_friday.services.library import grants
    state = {"yes": True}
    monkeypatch.setattr(grants, "allowed", lambda principal, path: state["yes"])
    return state


# ── H1: consent is read before EACH file ────────────────────────────────────────

def test_a_folder_removed_while_the_pass_waits_is_not_read_any_further(tmp_path):
    from agent_friday.services.library import indexer
    root = _folder(tmp_path, 5)
    st = _store()
    state = {"on": True, "seen": []}

    class Hooks:
        def total(self, n):
            pass

        def before(self, p):
            state["seen"].append(p.name)
            if len(state["seen"]) == 2:
                state["on"] = False                 # the owner removes the folder during the (long) wait

        def done(self, p, r):
            pass
    indexer.sweep_scope(st, root, allowed=lambda p: state["on"], hooks=Hooks())
    assert st.counts()["indexed"] == 1, "only the file read before the removal is in"


def test_a_never_send_mark_added_mid_pass_skips_that_file_but_not_the_rest(tmp_path):
    from agent_friday.services.library import indexer
    root = _folder(tmp_path, 3)
    st = _store()
    deny = {"name": None}

    class Hooks:
        def total(self, n):
            pass

        def before(self, p):
            if p.name == "n1.txt":
                deny["name"] = p.name

        def done(self, p, r):
            pass
    indexer.sweep_scope(st, root, allowed=lambda p: p.name != deny["name"], hooks=Hooks())
    assert {d["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for d in st.list_documents()} == {"n0.txt", "n2.txt"}


def test_triage_leaves_a_document_alone_when_consent_no_longer_covers_it(tmp_path):
    from agent_friday.services.library import indexer, triage
    root = _folder(tmp_path, 2)
    st = _store()
    indexer.sweep_scope(st, root)
    assert triage.triage_pending(st, allowed=lambda p: False) == 0
    assert len(st.untriaged()) == 2


# ── H2: a draft is checked again right before it is written ──────────────────────

def test_a_draft_finished_after_the_document_was_forgotten_is_never_written(consent):
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")

    def slow_model(system, user, model):
        staging.purge_document(did)                  # the owner pressed Forget while the model waited for idle
        return {"facts": [FACT]}
    assert staging.draft_document(st, st.get_document(did), "m", slow_model) == []
    assert staging.list_items() == []
    assert did in staging._gone_ids()


def test_a_draft_is_dropped_when_consent_was_withdrawn_or_the_bytes_changed(consent):
    from agent_friday.services.library import staging
    st = _store()
    a, b = _doc(st, "a"), _doc(st, "b")

    def withdrawn(system, user, model):
        consent["yes"] = False
        return {"facts": [FACT]}
    assert staging.draft_document(st, st.get_document(a), "m", withdrawn) == []
    consent["yes"] = True

    def edited(system, user, model):
        st.x("UPDATE documents SET sha256=? WHERE id=?", ("different", b))
        return {"facts": [FACT]}
    assert staging.draft_document(st, st.get_document(b), "m", edited) == []
    assert staging.list_items() == []


def test_a_tier_that_turned_private_or_a_tombstone_drops_the_draft(consent):
    from agent_friday.services.library import staging
    st = _store()
    a, b = _doc(st, "a"), _doc(st, "b")

    def now_private(system, user, model):
        st.x("UPDATE doc_meta SET sensitivity=2 WHERE doc_id=?", (a,))
        return {"facts": [FACT]}
    assert staging.draft_document(st, st.get_document(a), "m", now_private) == []
    doc_b = st.get_document(b)
    st.x("INSERT INTO tombstones(sha256, path, ts) VALUES(?,?,?)", (doc_b["sha256"], doc_b["path"], 1.0))
    assert staging.draft_document(st, doc_b, "m", lambda *x: {"facts": [FACT]}) == []


def test_no_draft_is_written_for_a_forgotten_id_even_by_a_late_writer(consent):
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    doc = st.get_document(did)
    staging.purge_document(did)
    checked = {"summary": None, "facts": [dict(FACT, block_id=1)]}
    assert staging.write_items(doc, None, "m", checked) == []
    staging.purge_drafts(did)                       # a re-read is not a forget: it does not tombstone
    assert did in staging._gone_ids()


# ── M1: forget-person finds and purges the staged facts and quotes ───────────────

def test_forgetting_a_person_finds_and_purges_the_staged_facts_that_name_them(consent):
    from agent_friday.services.library import forget, staging
    st = _store()
    did = _doc(st, "a", "Jere signed the lease on the first of March.\nThe valve needs a repair.")
    jere = {"subject": "Jere", "predicate": "signed", "object": "the lease",
            "quote": "Jere signed the lease on the first of March."}
    staging.draft_document(st, st.get_document(did), "m", lambda *x: {"facts": [jere, FACT]})
    assert forget.people_find(["Jere"], []) >= 1
    forget.people_purge(["Jere"], [])
    left = staging.list_items()
    assert all("jere" not in str(i).lower() for i in left)
    assert forget.people_find(["Jere"], []) == 0


# ── M2: a re-read file loses the drafts made from its old text ───────────────────

def test_editing_a_file_drops_the_drafts_made_from_its_old_text(tmp_path):
    from agent_friday.services.library import indexer, staging
    root = _folder(tmp_path, 1)
    st = _store()
    indexer.sweep_scope(st, root)
    doc = st.list_documents()[0]
    staging.write_items(doc, None, "m", {"summary": None, "facts": [dict(FACT, block_id=1)]})
    assert staging.list_items(doc_id=doc["id"])
    path = root / "n0.txt"
    path.write_text("Meeting notes rewritten completely, with other words about cranes and rope.", encoding="utf-8")
    indexer.index_file(st, path, root)
    assert staging.list_items(doc_id=doc["id"]) == []
    assert doc["id"] not in staging._gone_ids(), "the document is still there and may be drafted again"


# ── M3: drafting honours the Library's holds ─────────────────────────────────────

def test_drafting_waits_for_the_owners_pause_a_meeting_off_the_record_and_battery(monkeypatch):
    from agent_friday.services import turn_queue as tq
    from agent_friday.services.library import index_job, runtime, staging
    assert staging.holds("owner") == ""
    index_job.pause("owner")
    assert staging.holds("owner") == index_job.OWNER_HOLD
    index_job.resume("owner")
    monkeypatch.setattr(tq, "background_pause_reason", lambda: "You are in a meeting")
    assert staging.holds("owner") == "paused: you are in a meeting"
    monkeypatch.setattr(tq, "background_pause_reason", lambda: "")
    monkeypatch.setattr(runtime, "machine_is_free", lambda: (False, "off the record"))
    assert staging.holds("owner") == "paused: off the record"


def test_the_drafting_pass_blocks_between_documents_until_the_hold_is_lifted(monkeypatch):
    from agent_friday.services.library import index_job, staging
    index_job.pause("owner")
    slept = []
    monkeypatch.setattr(staging, "_sleep", lambda s: (slept.append(s), index_job.resume("owner")))
    staging.wait_for_holds("owner")
    assert len(slept) == 1


# ── M4: files deleted from disk leave the Library even while reading is paused ───

def test_a_deleted_file_leaves_the_library_while_reading_is_paused(tmp_path):
    from agent_friday.services.library import grants, index_job, indexer, runtime
    root = _folder(tmp_path, 2)
    grants.add_scope("owner", str(root))
    st = _store()
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    assert st.counts()["indexed"] == 2
    (root / "n0.txt").unlink()
    index_job.pause("owner")
    runtime.resweep("owner")
    assert st.counts()["indexed"] == 1


# ── M5: Resume undoes only the Pause; a removed folder's records go ──────────────

def test_a_folder_that_left_the_library_leaves_the_queue_lists_too(tmp_path):
    from agent_friday.services.library import index_job, runtime
    root = _folder(tmp_path, 1)
    pr = index_job.progress_for("owner")
    pr.set(str(root), state=index_job.STOPPED, name="Notes")
    pr.save()
    runtime.purge_uncovered("owner")
    assert str(root) not in index_job.folders("owner")


# ── L1, L2, L3, L5, L6, L8 ───────────────────────────────────────────────────────

def test_a_cloud_voice_hears_counts_only_even_when_the_activity_read_fails(monkeypatch):
    from agent_friday.services import activity_lane, agent
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "anthropic")}))
    snap = {"running": [{"id": "j", "kind": "library_index", "state": "running", "label": "Indexing Divorce 2026"}],
            "queued": [], "count": 0, "jobs": []}
    monkeypatch.setattr(agent, "local_queue_snapshot", lambda: snap)

    def boom(**kw):
        raise RuntimeError("activity unavailable")
    monkeypatch.setattr(activity_lane, "spoken_running", boom)
    said = agent._tool_queue_status({})
    assert "Divorce" not in said and "1 running" in said


def test_a_held_folder_is_listed_under_its_cards_id_so_it_is_counted_once(tmp_path):
    from agent_friday.services.library import index_job
    root = _folder(tmp_path, 1)
    pr = index_job.progress_for("owner")
    pr.set(str(root), state=index_job.PAUSED, name="Notes", hold="paused by you", card_id="card-1")
    rows = index_job.listed_jobs()
    assert [r["id"] for r in rows] == ["card-1"]
    assert index_job.cancel_listed("card-1") is True


def test_a_single_file_is_not_a_perpetual_empty_pass(tmp_path):
    from agent_friday.services.library import changes, grants, indexer, runtime
    f = tmp_path / "one.txt"
    f.write_text("A single note about the harbor and the tides, with a marker zebra.", encoding="utf-8")
    ev = grants.add_scope("owner", str(f))
    st = _store()
    indexer.sweep_scope(st, f, allowed=lambda p: grants.allowed("owner", p))
    assert st.counts()["indexed"] == 1
    assert changes.queue("owner", runtime.indexer_for("owner"), ev, st) == 0


def test_a_pass_with_nothing_to_draft_opens_no_card(monkeypatch):
    from agent_friday.services import agent
    from agent_friday.services.library import index_job, staging
    with agent.TASKS_LOCK:
        for k in [k for k, t in agent.TASKS.items() if t.get("job_kind") == "library_stage"]:
            agent.TASKS.pop(k)
    monkeypatch.setattr(staging, "_local_model", lambda: "m")
    monkeypatch.setattr(staging, "run_pass", lambda *a, **k: pytest.fail("nothing was eligible"))
    staging.Stager("owner")._one("/scope", index_job)
    with agent.TASKS_LOCK:
        assert not [t for t in agent.TASKS.values() if t.get("job_kind") == "library_stage"]


def test_the_model_reads_no_more_than_the_classifier_read(consent):
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "long", "\n".join("Line %d of a long public note about the harbor." % i for i in range(80)))
    assert staging.MAX_CHARS <= 4000
    assert len(staging._blocks(st, st.get_document(did))) == 40
    assert len(staging._prompt_text(staging._blocks(st, st.get_document(did)))) <= 4000


def test_reviewing_a_draft_is_a_gated_library_action_and_the_server_starts_the_library():
    from pathlib import Path
    from agent_friday.governance import action_gate
    assert "library_draft_review" in action_gate.LIBRARY_SCREEN_ACTIONS
    root = Path(__file__).resolve().parents[2]
    assert '_gated("library_draft_review"' in (root / "src/agent_friday/routes/library.py").read_text(encoding="utf-8")
    assert "_library_runtime.start_background()" in (root / "src/agent_friday/server.py").read_text(encoding="utf-8")


# ── re-review conditions ─────────────────────────────────────────────────────────

def test_a_draft_is_dropped_when_a_block_the_model_read_was_deleted_by_forget_person(consent):
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")

    def slow_model(system, user, model):
        st.x("DELETE FROM blocks WHERE doc_id=? AND ord=0", (did,))   # forget-person removed the passage naming them
        return {"facts": [FACT]}
    assert staging.draft_document(st, st.get_document(did), "m", slow_model) == []
    assert staging.list_items() == []


def test_a_folder_with_a_glob_grant_is_covered_and_the_glob_judges_each_file(tmp_path):
    from agent_friday.services.library import grants, index_job, indexer
    root = tmp_path / "Mixed"
    write_docs(root, {"a.md": "Notes about the harbor and the tides.", "b.txt": "Other notes about cranes.",
                      "c.md": "More notes about the harbor and rope."})
    grants.add_scope("owner", str(root), glob="*.md")
    assert grants.scope_covered("owner", root) is True
    assert index_job.FolderJob("owner", str(root))._covered() is True
    st = _store()
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p),
                        covered=lambda s: grants.scope_covered("owner", s))
    names = sorted(d["path"].replace("\\", "/").rsplit("/", 1)[-1] for d in st.list_documents())
    assert names == ["a.md", "c.md"], "the .md files are read, the others skipped, the pass is not ended"


def test_a_removed_folder_is_no_longer_covered(tmp_path):
    from agent_friday.services.library import grants
    root = tmp_path / "Gone"
    write_docs(root, {"a.md": "Notes."})
    ev = grants.add_scope("owner", str(root), glob="*.md")
    assert grants.scope_covered("owner", root) is True
    grants.remove_scope("owner", ev["id"])
    assert grants.scope_covered("owner", root) is False


def test_a_held_drafting_pass_ends_its_job_and_waits_outside_the_queue(monkeypatch):
    from agent_friday.services import visible_jobs
    from agent_friday.services.library import index_job, staging
    calls, waited, state = [], [], {"hold": True}
    monkeypatch.setattr(staging, "_local_model", lambda: "m")
    monkeypatch.setattr(staging, "eligible", lambda store, scope: [1])
    monkeypatch.setattr(staging, "holds", lambda principal="owner": "paused by you" if state["hold"] else "")

    def fake_pass(principal, scope, progress=None, before=None, **kw):
        calls.append(visible_jobs.current() is not None)
        if len(calls) == 1:
            assert before() == "hold"
            return {"state": "held", "done": 0, "total": 2, "proposed": 0}
        return {"state": "done", "done": 2, "total": 2, "proposed": 1}

    def fake_wait(principal, vj=None):
        waited.append(visible_jobs.current() is None)       # no job, so no queue slot, while it waits
        state["hold"] = False
    monkeypatch.setattr(staging, "run_pass", fake_pass)
    monkeypatch.setattr(staging, "wait_for_holds", fake_wait)
    staging.Stager("owner")._one("/scope", index_job)
    assert calls == [True, True] and waited == [True]


def test_a_re_read_that_fails_still_drops_the_drafts_of_the_old_text(tmp_path, monkeypatch):
    from agent_friday.services.library import indexer, procrun, staging
    root = _folder(tmp_path, 1)
    st = _store()
    indexer.sweep_scope(st, root)
    doc = st.list_documents()[0]
    staging.write_items(doc, None, "m", {"summary": None, "facts": [dict(FACT, block_id=1)]})
    path = root / "n0.txt"
    path.write_text("Rewritten with other words about cranes, rope and weather.", encoding="utf-8")

    def broken(*a, **k):
        raise RuntimeError("the reader failed")
    monkeypatch.setattr(procrun, "run_task", broken)
    assert indexer.index_file(st, path, root)["state"] == "failed"
    assert staging.list_items(doc_id=doc["id"]) == []


def test_the_model_reads_the_same_raw_text_the_classifier_read(consent):
    from agent_friday.services.library import staging
    st = _store()
    padded = "\n".join("Line %d   " % i + " " * 400 + "end" for i in range(12))
    did = _doc(st, "padded", padded)
    blocks = staging._blocks(st, st.get_document(did))
    assert staging._prompt_text(blocks) == "\n".join(t for _i, t in blocks)[:staging.MAX_CHARS]
