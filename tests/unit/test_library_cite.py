"""Footnotes: labels become tokens, a token is live only when this turn's search
returned it, and a dead source never reads as a live one."""
from __future__ import annotations

import json
import os

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, make_pdf, release_library,
                                    write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services import agent
    from agent_friday.services.library import shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)      # the classifier has its own tests
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "local")}))
    yield
    release_library(fg, lstore)


def _setup(tmp_path):
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    write_docs(root, {"lease.pdf": make_pdf([["# Lease", "The tenant shall pay rent monthly."],
                                             ["# Termination", "Either party may end the lease with ninety days notice."]]),
                      "other.txt": "A note about gardens and compost."})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    return st, root


def _trace(question="how much notice to end the lease"):
    from agent_friday.services.library import tools
    out = tools.search_library({"question": question})
    return [{"name": "search_library", "input": {"question": question}, "result": out[:2000]}], out


def test_the_tool_result_names_every_ref_in_its_first_line_ahead_of_any_clip(tmp_path):
    _setup(tmp_path)
    trace, out = _trace()
    meta = json.loads(out.split("\n", 1)[0])
    assert meta["refs"] and all(v.startswith("lib:") for v in meta["refs"].values())
    from agent_friday.services.library import cite
    assert cite.refs_from_trace(trace) == meta["refs"]


def test_a_label_becomes_a_lib_token_for_this_turns_passage(tmp_path):
    from agent_friday.services.library import cite
    _setup(tmp_path)
    trace, _ = _trace()
    ref = cite.refs_from_trace(trace)["1.1"]
    out, flagged = cite.finish("Ninety days is needed [1.1]. Version [9.9] is unrelated.", trace)
    assert f"[{ref}]" in out and "[9.9]" in out and flagged == []


# -- test_lib_citation_unverified_unless_returned_this_turn --------------------------

def test_a_token_this_turn_did_not_return_is_unverified(tmp_path):
    from agent_friday.services.library import cite
    st, _ = _setup(tmp_path)
    trace, _ = _trace()
    real = next(iter(cite.refs_from_trace(trace).values()))
    other_doc = st.q("SELECT d.id, b.id bid FROM documents d JOIN blocks b ON b.doc_id=d.id WHERE d.title='other'")[0]
    fake = f"lib:{other_doc['id']}#{other_doc['bid']}"
    out, flagged = cite.finish(f"A [{real}] and B [{fake}].", trace)
    assert f"[{real}]" in out
    assert f"[unverified-{fake}]" in out and flagged == [fake]
    # With no search at all this turn, even a real block is not live.
    out2, flagged2 = cite.finish(f"C [{real}].", [])
    assert f"[unverified-{real}]" in out2


def test_a_live_citation_is_recorded_for_revocation(tmp_path):
    from agent_friday.services.library import cite
    st, _ = _setup(tmp_path)
    trace, _ = _trace()
    ref = next(iter(cite.refs_from_trace(trace).values()))
    cite.finish(f"Yes [{ref}].", trace, conversation_id="conv-1")
    doc, blk = ref[4:].split("#")
    row = st.q("SELECT * FROM cited_in WHERE conversation_id='conv-1'")[0]
    assert (row["doc_id"], row["block_id"], row["conversation_id"]) == (int(doc), int(blk), "conv-1")


# -- test_lib_citation_rechecked_before_display ---------------------------------------

def test_a_document_forgotten_mid_turn_leaves_no_live_chip(tmp_path):
    from agent_friday.services.library import cite, forget
    st, _ = _setup(tmp_path)
    trace, _ = _trace()
    ref = next(iter(cite.refs_from_trace(trace).values()))
    forget.forget_document("owner", int(ref[4:].split("#")[0]))
    out, _ = cite.finish(f"Yes [{ref}].", trace)
    assert cite.GONE in out and "lib:" not in out


def test_a_removed_consent_or_a_changed_file_kills_the_chip(tmp_path):
    from agent_friday.services.library import cite, grants
    st, root = _setup(tmp_path)
    trace, _ = _trace()
    ref = next(iter(cite.refs_from_trace(trace).values()))
    (root / "lease.pdf").write_bytes((root / "lease.pdf").read_bytes() + b"\n%edited")
    out, _ = cite.finish(f"Yes [{ref}].", trace)
    assert cite.GONE in out
    # restore the file's stamp, then withdraw the consent instead
    st2, _ = st, None
    trace2, _ = _trace()
    for a in grants.active_scopes("owner"):
        grants.remove_scope("owner", a["id"])
    out2, _ = cite.finish(f"Yes [{ref}].", trace)
    assert cite.GONE in out2


def test_the_observer_gets_no_chips(tmp_path, monkeypatch):
    from agent_friday.services.library import cite, principal
    _setup(tmp_path)
    trace, _ = _trace()
    ref = next(iter(cite.refs_from_trace(trace).values()))
    monkeypatch.setattr(principal, "current", lambda: None)
    out, _ = cite.finish(f"Yes [{ref}].", trace)
    assert cite.GONE in out


def test_spoken_text_never_carries_a_token():
    from agent_friday.services.library import cite
    assert cite.speakable("Page fourteen says so [lib:3#44] and more.") == "Page fourteen says so  and more."


def test_the_citation_grammar_knows_the_library_kinds():
    from agent_friday.services import citation_enforcement as ce
    from agent_friday.services import model_router
    assert ce.count_citations("a [lib:1#2] b [unverified-lib:3#4]") == 2
    assert "[1.2]" in model_router.CITATION_INSTRUCTIONS and "Library" in model_router.CITATION_INSTRUCTIONS
