"""Indexed file text never appears in a cloud payload without a matching file grant (the canary test).

A distinctive string goes into one body of text. The folder is read, noted by Laya's triage, and drafted from
by a (fake) local model; every read-out the cloud side could receive is then scanned for it: the Library's
cloud search, the queue read-out the voice tools return, the queue rows, the status line. The network
primitives are replaced by recorders for the whole run. With a file grant on that one document the canary may
reach the cloud search result (the control), and not before.
"""
from __future__ import annotations

import json
import socket
import urllib.request

import pytest

from library_queue_helpers import install, teardown
from tests.library_fixtures import install_fake_encoder, isolate_library, release_library, write_docs

CANARY = "CANARY-KW2-9d41e6-harbor-ledger-balance"
BODY = ("The harbor inspection found a cracked hull on pier four. The %s was filed with the finance office "
        "and the repair crew will return on the first of March to replace the valve.\n\n"
        "Further notes on tides and cranes for the quarterly review." % CANARY)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    queue = install(monkeypatch)
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_CURRENT_PROVIDER", type("V", (), {"get": staticmethod(lambda: "anthropic")}))
    from agent_friday.services.library import tools
    tools._TURN_SENT.clear()
    yield queue
    tools._TURN_SENT.clear()
    teardown()
    release_library(fg, lstore)


@pytest.fixture
def watched(monkeypatch):
    attempts = []

    def trip(*a, **k):
        attempts.append(a[:2])
        raise AssertionError("something tried to reach the network")
    monkeypatch.setattr(socket.socket, "connect", trip)
    monkeypatch.setattr(socket.socket, "connect_ex", trip)
    monkeypatch.setattr(socket, "create_connection", trip)
    monkeypatch.setattr(urllib.request, "urlopen", trip)
    return attempts


def _read_everything(tmp_path):
    """Read a folder holding the canary, note it, and draft from it with a fake local model."""
    from agent_friday.services.library import grants, indexer, shelf, staging, triage
    from agent_friday.services.library.store import store_for
    root = tmp_path / "Lib"
    docs = write_docs(root, {"harbor.txt": BODY})
    grants.add_scope("owner", str(root))
    st = store_for("owner")
    indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p), classify=shelf.chooser("owner"))
    triage.triage_pending(st)
    seen_by_model = []

    def local_model(system, user, model):
        seen_by_model.append(user)
        return {"summary": "The harbor needs a hull repair and a valve replacement in March.",
                "evidence": ["The harbor inspection found a cracked hull on pier four."],
                "facts": [{"subject": "pier four", "predicate": "has", "object": "a cracked hull",
                           "quote": "The harbor inspection found a cracked hull on pier four."}]}
    staging.run_pass("owner", str(root.resolve()), complete=local_model, model="a-local-model")
    return root, docs["harbor.txt"], seen_by_model


def _cloud_search(monkeypatch, allow=True):
    from agent_friday.services import file_grants as fg
    from agent_friday.services.library import tools
    sent = []
    monkeypatch.setattr(fg, "on_file_read", lambda path, text: sent.append(text))
    monkeypatch.setattr(tools, "_settings", lambda: {"library_cloud_answers": allow})
    return tools.search_library({"question": "harbor inspection cracked hull valve"}), sent


def test_the_cloud_search_never_carries_the_text_without_a_grant(tmp_path, monkeypatch, watched):
    _read_everything(tmp_path)
    out, sent = _cloud_search(monkeypatch)
    assert CANARY not in out and sent == []
    assert "no cloud permission" in out or "withheld" in out or "stay on this PC" in out


def test_even_with_cloud_answers_switched_on_a_document_without_a_grant_stays_home(tmp_path, monkeypatch, watched):
    _read_everything(tmp_path)
    out, sent = _cloud_search(monkeypatch, allow=True)
    assert CANARY not in out and CANARY not in " ".join(sent)


def test_with_a_grant_on_that_document_the_text_may_reach_the_cloud_search(tmp_path, monkeypatch, watched):
    from agent_friday.services import file_grants as fg
    root, path, _seen = _read_everything(tmp_path)
    fg.create_file_grant(str(path))
    out, sent = _cloud_search(monkeypatch, allow=True)
    assert CANARY in out and any(CANARY in t for t in sent), "the control: a granted document is readable"


def test_what_the_queue_tools_and_rows_say_never_includes_the_text(tmp_path, _env, monkeypatch, watched):
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services import queue_tools
    from agent_friday.services import seat_supervisor as ss
    from agent_friday.services.library import index_job, runtime
    _read_everything(tmp_path)
    index_job.pause("owner")
    snap = ls.queue_snapshot()
    spoken = ss.spoken_summary(snap)
    said = [spoken, index_job.status_line("owner"), queue_tools.handle_local_work({"action": "status"}),
            queue_tools.handle_local_work({"action": "pause"}), json.dumps(snap, default=str),
            json.dumps(index_job.folders("owner"), default=str),
            json.dumps(index_job.first_answer_report("owner"), default=str),
            json.dumps(index_job.listed_jobs(), default=str)]
    for text in said:
        assert CANARY not in text


def test_the_notes_laya_keeps_are_labels_and_numbers_not_text(tmp_path, watched):
    from agent_friday.services.library.store import store_for
    _read_everything(tmp_path)
    st = store_for("owner")
    for row in st.q("SELECT * FROM doc_meta"):
        assert CANARY not in json.dumps({k: row[k] for k in row.keys()}, default=str)
    cols = {r[1] for r in st.q("PRAGMA table_info(doc_meta)")}
    assert cols == {"doc_id", "doc_type", "type_score", "sensitivity", "dup_of", "dup_kind", "dup_score",
                    "degraded", "versions", "triage_version", "triaged_at"}


def test_the_drafts_stay_in_staging_and_go_only_to_the_local_model(tmp_path, watched):
    from agent_friday.services.library import staging
    root, path, seen = _read_everything(tmp_path)
    assert seen and CANARY in seen[0], "the local model reads the document"
    items = staging.list_items()
    assert items and all(i["provenance"]["model"] == "a-local-model" for i in items)
    assert str(path) not in seen[0]


def test_no_voice_or_chat_tool_hands_the_drafts_to_a_model():
    """The drafts are read by the owner's own screens and routes; no registered tool returns them."""
    import inspect
    from agent_friday.services import queue_tools
    from agent_friday.services.library import tools
    for mod in (queue_tools, tools):
        src = inspect.getsource(mod)
        assert "list_items" not in src and "staging" not in src, mod.__name__


def test_nothing_in_the_whole_run_tried_to_reach_the_network(tmp_path, monkeypatch, watched):
    _read_everything(tmp_path)
    _cloud_search(monkeypatch)
    assert watched == []
