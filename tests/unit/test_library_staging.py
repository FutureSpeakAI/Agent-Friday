"""Drafts from the local model go to wiki/_staging with a span for every fact, and nowhere else.

The model is a function the test hands in; nothing loads a model or opens a socket. Documents are made
directly in the index so no reader child is needed. Fixtures are synthetic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_friday.services.library import staging as _staging
from tests.library_fixtures import install_fake_encoder, isolate_library, release_library

_REAL_LEARNING_OFF = _staging._learning_off

TEXT = ("The harbor inspection found a cracked hull on pier four.\n"
        "The repair crew will return on the first of March to replace the valve.\n"
        "Budget approval remains with the finance office.")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import shelf, staging, triage
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(staging, "_learning_off", lambda: False)
    # These documents have no folder in the ledger; consent is its own test (test_library_kw2_review.py).
    from agent_friday.services.library import grants
    monkeypatch.setattr(grants, "allowed", lambda principal, path: True)
    triage.reset_heads()
    yield
    triage.reset_heads()
    release_library(fg, lstore)


def _store():
    from agent_friday.services.library.store import store_for
    return store_for("owner")


def _doc(st, name, text=TEXT, *, sha=None, shelf="open", tier=1, dup_of=None):
    """An indexed, triaged document: one block per line, made without a reader."""
    fid = st.folder_id("/scope", "")
    did = st.upsert_document("/scope/%s.txt" % name, sha256=sha or name.encode().hex(), size=300, mtime=5.0, kind="text",
                             ext="txt", scope="/scope", folder_id=fid, title=name, shelf=shelf)
    st.set_state(did, "indexed")
    for i, line in enumerate(text.split("\n")):
        st.x("INSERT INTO blocks(doc_id, ord, kind, text) VALUES(?,?,?,?)", (did, i, "paragraph", line))
    st.set_doc_meta(did, doc_type="note", type_score=0.5, sensitivity=tier, dup_of=dup_of, dup_kind=None,
                    dup_score=None, degraded=False, versions={"triage": 1})
    return did


def _blocks(st, did):
    from agent_friday.services.library import staging
    return staging._blocks(st, st.get_document(did))


GOOD_FACT = {"subject": "pier four", "predicate": "has", "object": "a cracked hull",
             "quote": "The harbor inspection found a cracked hull on pier four."}


def test_a_fact_with_a_sentence_from_the_document_is_kept_with_its_block():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    out = staging.check_draft({"facts": [GOOD_FACT]}, _blocks(st, did))
    assert len(out["facts"]) == 1
    f = out["facts"][0]
    first = _blocks(st, did)[0][0]
    assert f["block_id"] == first and f["quote"] == GOOD_FACT["quote"]


def test_a_fact_whose_quote_is_not_in_the_document_is_dropped():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    invented = dict(GOOD_FACT, quote="The harbor master resigned in a dispute over fuel prices.")
    assert staging.check_draft({"facts": [invented]}, _blocks(st, did))["facts"] == []


def test_a_fact_with_no_quote_or_a_quote_too_short_to_mean_anything_is_dropped():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    facts = [dict(GOOD_FACT, quote=""), dict(GOOD_FACT, quote="hull"), {k: v for k, v in GOOD_FACT.items() if k != "quote"}]
    assert staging.check_draft({"facts": facts}, _blocks(st, did))["facts"] == []


def test_a_fact_missing_a_part_or_with_a_huge_part_is_dropped_and_repeats_are_one():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    facts = [dict(GOOD_FACT, object=""), dict(GOOD_FACT, subject="x" * 500), GOOD_FACT, dict(GOOD_FACT)]
    assert len(staging.check_draft({"facts": facts}, _blocks(st, did))["facts"]) == 1


def test_a_quote_is_matched_whatever_the_whitespace():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    spaced = dict(GOOD_FACT, quote="The harbor   inspection found a\ncracked hull on pier four.")
    assert len(staging.check_draft({"facts": [spaced]}, _blocks(st, did))["facts"]) == 1


def test_a_summary_needs_at_least_one_verified_span():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    blocks = _blocks(st, did)
    said = "The harbor needs a hull repair, scheduled for March, with the budget still to be approved."
    assert staging.check_draft({"summary": said, "evidence": []}, blocks)["summary"] is None
    assert staging.check_draft({"summary": said, "evidence": ["Something the document never says at all."]},
                               blocks)["summary"] is None
    ok = staging.check_draft({"summary": said, "evidence": [GOOD_FACT["quote"], "not in the document either way"]}, blocks)
    text, spans = ok["summary"]
    assert text == said and [s["quote"] for s in spans] == [GOOD_FACT["quote"]]


def test_junk_from_the_model_yields_nothing_and_does_not_raise():
    from agent_friday.services.library import staging
    st = _store()
    blocks = _blocks(st, _doc(st, "a"))
    for junk in (None, [], "text", {"facts": "x"}, {"facts": [None, 3, "x"]}, {"summary": 7, "evidence": 3}):
        out = staging.check_draft(junk, blocks)
        assert out == {"summary": None, "facts": []}


def test_staging_is_the_wiki_staging_folder_under_the_friday_home():
    from agent_friday.services.library import staging
    assert staging.staging_dir() == Path(os.environ["FRIDAY_HOME"]) / "wiki" / "_staging"
    from agent_friday.services.knowledge_graph import wiki_graph
    assert "_staging" in wiki_graph.SKIP_DIRS


def test_a_draft_item_carries_provenance_and_a_span_and_nothing_else_is_written(tmp_path):
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    before = (st.counts(), [tuple(r) for r in st.q("SELECT id, shelf, state, title FROM documents")])
    seen = {}

    def model(system, user, name):
        seen.update(system=system, user=user, name=name)
        return {"summary": "The harbor needs a hull repair and a valve replacement in March.",
                "evidence": [GOOD_FACT["quote"]], "facts": [GOOD_FACT]}
    ids = staging.draft_document(st, st.get_document(did), "local-model-x", model)
    assert sorted(ids) == sorted(i["id"] for i in staging.list_items())
    assert len(ids) == 2
    fact = next(i for i in staging.list_items() if i["kind"] == "fact")
    prov = fact["provenance"]
    assert fact["status"] == "proposed"
    assert fact["citations"][0]["quote"] == GOOD_FACT["quote"] and fact["citations"][0]["block_id"]
    assert prov["doc_id"] == did and prov["sha256"] == "a".encode().hex() and prov["model"] == "local-model-x"
    assert prov["extractor"] == staging.EXTRACTOR and prov["authority"] == "model-inferred"
    assert prov["observed_at"] == 5.0 and prov["sensitivity"] == 1
    assert seen["name"] == "local-model-x"
    assert "/scope" not in seen["user"] and "a.txt" not in seen["user"], "the path is not sent to the model"
    wiki = Path(os.environ["FRIDAY_HOME"]) / "wiki"
    assert {p.relative_to(wiki).parts[0] for p in wiki.rglob("*") if p.is_file()} == {"_staging"}
    after = (st.counts(), [tuple(r) for r in st.q("SELECT id, shelf, state, title FROM documents")])
    assert after == before, "drafting does not touch the Library's own rows"


def test_only_public_open_original_documents_are_drafted_from():
    from agent_friday.services.library import staging
    st = _store()
    ok = _doc(st, "ok")
    _doc(st, "private", tier=2)
    _doc(st, "vault", shelf="vault")
    _doc(st, "copy", dup_of=ok)
    assert [d["id"] for d in staging.eligible(st)] == [ok]


def test_a_pass_drafts_each_document_once_and_not_again_for_the_same_bytes():
    from agent_friday.services.library import staging
    st = _store()
    _doc(st, "a")
    calls = []

    def model(system, user, name):
        calls.append(name)
        return {"facts": [GOOD_FACT]}
    out = staging.run_pass("owner", "/scope", complete=model, model="m")
    assert out == {"state": "done", "done": 1, "total": 1, "proposed": 1}
    again = staging.run_pass("owner", "/scope", complete=model, model="m")
    assert again["total"] == 0 and len(calls) == 1


def test_a_changed_document_is_drafted_from_again():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    calls = []
    staging.run_pass("owner", "/scope", complete=lambda s, u, m: calls.append(1) or {}, model="m")
    st.x("UPDATE documents SET sha256='new-bytes' WHERE id=?", (did,))
    staging.run_pass("owner", "/scope", complete=lambda s, u, m: calls.append(1) or {}, model="m")
    assert len(calls) == 2


def test_learning_turned_off_means_no_drafts(monkeypatch):
    from agent_friday.services.library import staging
    st = _store()
    _doc(st, "a")
    monkeypatch.setattr(staging, "_learning_off", lambda: True)
    out = staging.run_pass("owner", "/scope", complete=lambda *a: pytest.fail("the model was asked"), model="m")
    assert out["state"] == "off" and staging.list_items() == []


def test_the_setting_that_means_off_is_library_kg_learn(monkeypatch):
    import agent_friday.core as core
    from agent_friday.services.library import staging
    monkeypatch.setattr(staging, "_learning_off", _REAL_LEARNING_OFF)
    monkeypatch.setattr(core, "_load_settings", lambda: {"library_kg_learn": "off"})
    assert staging._learning_off() is True
    monkeypatch.setattr(core, "_load_settings", lambda: {"library_kg_learn": ""})
    assert staging._learning_off() is False


def test_with_no_local_model_the_pass_waits_and_says_so(monkeypatch):
    from agent_friday.services.library import staging
    st = _store()
    _doc(st, "a")
    monkeypatch.setattr(staging, "_local_model", lambda: None)
    out = staging.run_pass("owner", "/scope", complete=lambda *a: pytest.fail("the model was asked"))
    assert out["state"] == "waiting_model" and staging.list_items() == []


def test_the_default_model_call_is_the_local_one(monkeypatch):
    from agent_friday.services import local_call
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    got = {}

    def fake(system, user, model, **kw):
        got.update(model=model)
        return {"facts": [GOOD_FACT]}
    monkeypatch.setattr(local_call, "call_json", fake)
    staging.draft_document(st, st.get_document(did), "local-model-x")
    assert got["model"] == "local-model-x"


def test_review_accepts_defers_and_rejects_and_refuses_what_is_not_a_draft():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    ids = staging.draft_document(st, st.get_document(did), "m", lambda *a: {"facts": [GOOD_FACT, dict(
        GOOD_FACT, subject="valve", object="a replacement", quote="The repair crew will return on the first of March to replace the valve.")]})
    assert staging.counts()["proposed"] == 2
    assert staging.review(ids[0], "accept") == {"ok": True, "status": "accepted"}
    assert staging.review(ids[1], "reject") == {"ok": True, "status": "rejected"}
    # a rejection keeps a tombstone with none of the draft's text, so the same draft is not proposed again
    assert staging.counts() == {"proposed": 0, "accepted": 1, "rejected": 1, "deferred": 0}
    kept = staging.list_items("rejected")[0]
    assert "citations" not in kept and "subject" not in kept and kept["id"] == ids[1]
    assert staging.review(ids[0], "defer")["status"] == "deferred"
    for bad in ("../../etc/passwd", "lib-1-x-summary/../../y", "", "lib-9-zz-fact"):
        assert staging.review(bad, "accept")["ok"] is False
    assert staging.review(ids[0], "publish")["ok"] is False


def test_accepting_a_draft_writes_no_wiki_page():
    from agent_friday.services.library import staging
    st = _store()
    did = _doc(st, "a")
    ids = staging.draft_document(st, st.get_document(did), "m", lambda *a: {"facts": [GOOD_FACT]})
    staging.review(ids[0], "accept")
    wiki = Path(os.environ["FRIDAY_HOME"]) / "wiki"
    assert [p for p in wiki.rglob("*.md")] == []


def test_removing_a_document_removes_its_drafts_and_its_note_that_it_was_drafted():
    from agent_friday.services.library import forget, staging
    st = _store()
    a, b = _doc(st, "a"), _doc(st, "b")
    for d in (a, b):
        staging.draft_document(st, st.get_document(d), "m", lambda *x: {"facts": [GOOD_FACT]})
    staging.run_pass("owner", "/scope", complete=lambda *x: {}, model="m")
    forget.remove_document("owner", a)
    left = staging.list_items()
    assert left and all(i["provenance"]["doc_id"] == b for i in left)
    assert str(a) not in staging._read_index()


def test_forgetting_a_document_removes_its_drafts_too():
    from agent_friday.services.library import forget, staging
    st = _store()
    a = _doc(st, "a")
    staging.draft_document(st, st.get_document(a), "m", lambda *x: {"facts": [GOOD_FACT]})
    assert staging.list_items()
    forget.forget_document("owner", a)
    assert staging.list_items() == []


def test_staging_has_no_way_to_reach_a_cloud_model():
    import re
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "library" / "staging.py")
    text = src.read_text(encoding="utf-8")
    assert not re.search(r"anthropic|openai|gemini|model_router|requests|urllib|socket|http", text, re.I)
