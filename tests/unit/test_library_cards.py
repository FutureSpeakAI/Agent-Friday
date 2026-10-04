"""Changes to the Library that Friday proposes are one card each, approved on
screen only; voice, chat and a bare "owner" cannot approve them."""
from __future__ import annotations

import pytest

from tests.library_fixtures import isolate_library, release_library, write_docs


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    from agent_friday.services import dissent_gate as dg
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    yield
    release_library(fg, lstore)


def _tool(inp):
    from agent_friday.services import file_grant_requests as fgr
    return fgr.handle(inp)


def _card(kind):
    from agent_friday.services import approvals
    rows = approvals.list_approvals(status="pending", kind=kind)
    assert len(rows) == 1, rows
    return rows[0]


def test_the_library_actions_are_part_of_the_file_access_tool():
    from agent_friday.services import file_grant_requests as fgr
    enum = fgr.TOOLS[0]["input_schema"]["properties"]["action"]["enum"]
    for a in ("library_add", "library_list", "library_remove", "library_forget"):
        assert a in enum
    assert not any(t["name"].startswith("library_") for t in fgr.TOOLS)    # no separate grant-like tool


def test_library_add_raises_one_card_and_adds_nothing(tmp_path):
    from agent_friday.services.library import grants
    write_docs(tmp_path / "Reporting", {"a.txt": "hello there"})
    out = _tool({"action": "library_add", "items": [{"path": str(tmp_path / "Reporting")}], "reason": "for the brief"})
    assert "card" in out and "on screen" in out
    card = _card("library_add_request")
    assert card["payload"]["items"][0]["type"] == "folder"
    assert grants.active_scopes("owner") == []


@pytest.mark.parametrize("who", ["owner", "voice", "chat", "friday", "owner:chat"])
def test_only_the_screen_click_approves_an_add(tmp_path, who):
    from agent_friday.services import approvals
    from agent_friday.services.library import grants
    write_docs(tmp_path / "Reporting", {"a.txt": "hello there"})
    _tool({"action": "library_add", "path": str(tmp_path / "Reporting")})
    card = _card("library_add_request")
    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by=who)
    assert not won
    assert grants.active_scopes("owner") == []


def test_the_screen_click_adds_what_the_card_listed_and_queues_reading(tmp_path, monkeypatch):
    from agent_friday.services import approvals
    from agent_friday.services.library import grants, runtime
    queued = []
    monkeypatch.setattr(runtime, "index_scope", lambda principal, ev: queued.append(ev["path"]))
    write_docs(tmp_path / "Reporting", {"a.txt": "hello there"})
    _tool({"action": "library_add", "path": str(tmp_path / "Reporting")})
    card = _card("library_add_request")
    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won
    scopes = grants.active_scopes("owner")
    assert [s["path"] for s in scopes] == [str((tmp_path / "Reporting").resolve())]
    assert scopes[0]["source"] == "card"
    assert queued == [scopes[0]["path"]]
    again = approvals.claim_for_execution(card["approval_id"])
    assert not again                                   # one decision, one action


def test_a_refused_path_raises_no_card(tmp_path):
    out = _tool({"action": "library_add", "path": str(tmp_path / "nowhere")})
    assert out.startswith("No card raised")
    from agent_friday.services import approvals
    assert approvals.list_approvals(status="pending", kind="library_add_request") == []


def test_remove_and_forget_name_the_document_and_need_the_click(tmp_path):
    from agent_friday.services import approvals
    from agent_friday.services.library import grants, indexer
    from agent_friday.services.library.store import store_for
    d = write_docs(tmp_path / "Lib", {"smith-file.txt": "Smith v. Jones settlement terms."})
    grants.add_scope("owner", str(tmp_path / "Lib"))          # a card names only what the owner has added
    st = store_for("owner")
    indexer.index_file(st, d["smith-file.txt"], tmp_path / "Lib")
    out = _tool({"action": "library_forget", "document": "smith"})
    assert "smith-file" in out and "Nothing is forgotten" in out
    card = _card("library_forget_request")
    assert "smith-file" in card["title"]
    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="voice")
    assert not won and len(st.list_documents()) == 1
    rec, won = approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won and st.list_documents() == []
    assert _tool({"action": "library_remove", "document": "smith"}).startswith("No card raised")


def test_library_list_says_plainly_when_empty_and_when_paused(tmp_path):
    from agent_friday.services.library import grants
    assert "empty" in _tool({"action": "library_list"})
    write_docs(tmp_path / "R", {"a.txt": "x y z"})
    grants.add_scope("owner", str(tmp_path / "R"))
    assert "R" in _tool({"action": "library_list"})
