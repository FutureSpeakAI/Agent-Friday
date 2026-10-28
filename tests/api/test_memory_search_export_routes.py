"""The owner's memory export route (routes/core_memory: GET /api/memory/export)
and the memory search panel's export and per-result actions (NS-15.11-1).

The route: the owner only, the search's bounds, an attachment in JSON or
Markdown, streamed with nothing written to disk on this PC. The panel: an
"Export these results" control and, on each result, the owner's corrections
(Edit, Wrong: delete, Keep/Discard, Delete this message, Approve/Discard)
through routes the app already registers, identical in index.html and
ui_parts/app.html.

Red on 05049f218: /api/memory/export did not exist (404 below), and the panel
had neither the export control nor any action on a result.
"""
from __future__ import annotations

import builtins
import io
import json
import re
import tempfile
from pathlib import Path

import pytest

from agent_friday.services import conversations as cv
from agent_friday.services import core_memory as cm
from agent_friday.services import memory_export as me
from agent_friday.services import memory_influence as mi
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_search as ms
from agent_friday.services import observer_access as obs
from agent_friday.services import prompt_freeze
from agent_friday.services import user_model as um

NON_LOOPBACK = "203.0.113.7"   # TEST-NET-3 (RFC 5737)
ROOT = Path(__file__).resolve().parents[2]
KEY = "sk-ant-api03-" + "Q7w" * 12


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(mi, "_path", lambda: tmp_path / "recall-log.jsonl")
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(mp, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(cv, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(ms, "_forgotten_names", lambda: set())
    monkeypatch.setattr(ms, "_forgotten_emails", lambda: set())
    monkeypatch.setattr(ms, "_documents", lambda q: [])
    monkeypatch.setattr(ms, "_memory", lambda: None)
    monkeypatch.setattr(me, "_all_documents", lambda: [])
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def _card():
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    cm.add("user", "Sails Wren with Ada Lovelace on Sundays.", actor="owner")


def test_json_export_is_an_attachment_with_the_search_results(client):
    _card()
    r = client.get("/api/memory/export?q=Wren&kinds=card&format=json")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    cd = r.headers.get("Content-Disposition", "")
    assert cd.startswith("attachment;") and re.search(r'filename="friday-memory-[\d-]+\.json"', cd), cd
    assert r.mimetype == "application/json" and r.headers.get("Cache-Control") == "no-store"
    doc = json.loads(r.get_data(as_text=True))
    assert doc["query"] == "Wren" and doc["kinds"] == ["card"]
    texts = sorted(i["text"] for i in doc["items"])
    assert texts == ["Keeps a sailing boat called Wren.", "Sails Wren with Ada Lovelace on Sundays."]
    for i in doc["items"]:
        assert i["kind"] == "card" and i["status"] == "current" and i["source"]


def test_markdown_export_is_grouped_by_kind(client):
    _card()
    cm.queue("user", "add", text="Wren needs new sails before spring.", source="dream:2026-09-03")
    r = client.get("/api/memory/export?format=md")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert r.headers["Content-Disposition"].rstrip('"').endswith(".md")
    assert r.mimetype == "text/markdown"
    md = r.get_data(as_text=True)
    assert md.startswith("# What Friday keeps about you")
    assert "## Memory card" in md and "## Waiting for your review" in md
    assert md.index("## Memory card") < md.index("## Waiting for your review")
    assert "- Keeps a sailing boat called Wren." in md and "Wren needs new sails before spring." in md


def test_the_export_leaves_out_forgotten_people_and_keys(client, monkeypatch):
    _card()
    cm.add("user", "The marina gate code login is " + KEY, actor="owner")
    monkeypatch.setattr(ms, "_forgotten_names", lambda: {"Ada Lovelace"})
    for fmt in ("json", "md"):
        for qs in ("", "&q=Wren", "&q=marina"):
            body = client.get("/api/memory/export?format=" + fmt + qs).get_data(as_text=True)
            assert "Lovelace" not in body, (fmt, qs)
            assert KEY not in body and "sk-ant-" not in body, (fmt, qs)
    doc = json.loads(client.get("/api/memory/export?format=json&kinds=card").get_data(as_text=True))
    assert "Keeps a sailing boat called Wren." in [i["text"] for i in doc["items"]]


def test_the_export_is_bounded(client, monkeypatch):
    for line in ("Keeps a sailing boat called Wren.", "Prefers tea to coffee.", "Walks to work on Mondays.",
                 "Plays the cello badly."):
        cm.add("user", line, actor="owner")
    monkeypatch.setattr(me, "EXPORT_MAX", 2)
    doc = json.loads(client.get("/api/memory/export?format=json&kinds=card").get_data(as_text=True))
    assert doc["count"] == 2 and len(doc["items"]) == 2 and doc["truncated"] is True


def test_nothing_is_written_to_disk_by_an_export(client, monkeypatch, tmp_path):
    _card()
    client.get("/api/memory/export?format=json")            # the stores' own first-open files, once
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    writes = []
    real_open = builtins.open

    def watching_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append((str(file), mode))
        return real_open(file, mode, *a, **k)

    def refuse(*a, **k):
        raise AssertionError("an export made a temporary file")

    monkeypatch.setattr(builtins, "open", watching_open)
    monkeypatch.setattr(io, "open", watching_open)          # Path.open / write_text go through io.open
    for name in ("NamedTemporaryFile", "TemporaryFile", "mkstemp", "SpooledTemporaryFile"):
        monkeypatch.setattr(tempfile, name, refuse)
    for fmt in ("json", "md"):
        r = client.get("/api/memory/export?format=" + fmt)
        assert r.status_code == 200
        assert r.is_streamed, "the export is streamed from memory"
        r.get_data()
    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(io, "open", real_open)
    assert writes == [], writes
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("qs", [
    "format=pdf", "q=" + "x" * 201, "kinds=card,nope", "since=yesterday", "until=2026-02-30",
])
def test_export_parameters_are_bounded(client, qs):
    r = client.get("/api/memory/export?" + qs)
    assert r.status_code == 400, (qs, r.status_code)
    assert r.get_json()["ok"] is False


def test_a_caller_who_is_not_signed_in_gets_401(client, monkeypatch):
    import agent_friday.core as core
    _card()
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "a-remote-key-for-this-test")
    r = client.get("/api/memory/export?format=json", environ_overrides={"REMOTE_ADDR": NON_LOOPBACK})
    assert r.status_code == 401
    assert "Wren" not in r.get_data(as_text=True) and "attachment" not in r.headers.get("Content-Disposition", "")


def test_an_observer_credential_gets_403(client, monkeypatch):
    _card()
    monkeypatch.setattr(obs, "verify", lambda token: True)
    monkeypatch.setattr(obs, "is_read_allowed", lambda method, path: True)
    r = client.get("/api/memory/export?format=json", headers={obs.HEADER: "fobs_test"})
    assert r.status_code == 403
    assert "Wren" not in r.get_data(as_text=True)


@pytest.mark.parametrize("principal", ["observer", "paired_phone", "remote"])
def test_only_the_owners_own_principal_exports(app, principal):
    from flask import g
    from agent_friday.routes import core_memory as routes
    with app.test_request_context("/api/memory/export"):
        g.friday_principal = principal
        r = routes.memory_export_route.__wrapped__()
    resp = r[0] if isinstance(r, tuple) else r
    assert (r[1] if isinstance(r, tuple) else resp.status_code) == 403


# ── the panel ─────────────────────────────────────────────────────────────────

def _panel(f):
    text = (ROOT / f).read_text(encoding="utf-8")
    assert "function MemorySearchPanel(" in text, f
    return text.split("function MemorySearchPanel(")[1].split("// End memory search panel")[0]


#: What each action on a result calls: (method, the Flask rule, a fragment the panel's fetch carries).
ACTIONS = [
    ("GET", "/api/memory/export", "'/api/memory/export?format='"),
    ("GET", "/api/core-memory", "apiFetch('/api/core-memory')"),
    ("POST", "/api/core-memory/edit", "'/api/core-memory/edit'"),
    ("POST", "/api/core-memory/delete", "'/api/core-memory/delete'"),
    ("POST", "/api/core-memory/review", "'/api/core-memory/review'"),
    ("DELETE", "/api/conversations/<cid>/messages/<mid>", "'/api/conversations/'"),
    ("DELETE", "/api/setup-chat/about/<fact_id>", "'/api/setup-chat/about/'"),
    ("POST", "/api/memory/proposals/approve", "'/api/memory/proposals/approve'"),
    ("POST", "/api/memory/proposals/reject", "'/api/memory/proposals/reject'"),
]


def test_the_panel_has_export_and_actions_identically_in_both_ui_files():
    a, b = _panel("index.html"), _panel("ui_parts/app.html")
    assert a == b, "the panel differs between index.html and ui_parts/app.html"
    for label in ("Export these results", "'JSON'", "'Markdown'", "'Edit'", "'Wrong: delete…'", "'Keep'",
                  "'Discard'", "'Delete this message…'", "'Approve'", "'Yes, delete it'"):
        assert label in a, label
    assert "{method:'DELETE'}" in a and "'/messages/'" in a


def test_each_action_in_the_panel_calls_a_registered_route(app):
    body = _panel("ui_parts/app.html")
    rules = {}
    for rule in app.url_map.iter_rules():
        rules.setdefault(rule.rule, set()).update(rule.methods or ())
    for method, rule, fragment in ACTIONS:
        assert fragment in body, (rule, "the panel does not call it")
        assert rule in rules and method in rules[rule], (method, rule, "is not a registered route")
    # Every /api/ path the panel names is the start of a registered route: no new or misspelt endpoint.
    shapes = [re.sub(r"<[^>]+>", "", r) for r in rules]
    for path in set(re.findall(r"'(/api/[^'?]*)", body)):
        assert any(s.startswith(path) for s in shapes), path
