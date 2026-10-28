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


def _page():
    """The headers Friday's own page sends through apiFetch: its session token."""
    import agent_friday.core as core
    return {"X-Friday-Token": core._current_api_token()}


def test_json_export_is_an_attachment_with_the_search_results(client):
    _card()
    r = client.get("/api/memory/export?q=Wren&kinds=card&format=json", headers=_page())
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
    r = client.get("/api/memory/export?format=md", headers=_page())
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
            body = client.get("/api/memory/export?format=" + fmt + qs, headers=_page()).get_data(as_text=True)
            assert "Lovelace" not in body, (fmt, qs)
            assert KEY not in body and "sk-ant-" not in body, (fmt, qs)
    doc = json.loads(client.get("/api/memory/export?format=json&kinds=card", headers=_page()).get_data(as_text=True))
    assert "Keeps a sailing boat called Wren." in [i["text"] for i in doc["items"]]


def test_the_export_is_bounded(client, monkeypatch):
    for line in ("Keeps a sailing boat called Wren.", "Prefers tea to coffee.", "Walks to work on Mondays.",
                 "Plays the cello badly."):
        cm.add("user", line, actor="owner")
    monkeypatch.setattr(me, "EXPORT_MAX", 2)
    doc = json.loads(client.get("/api/memory/export?format=json&kinds=card", headers=_page()).get_data(as_text=True))
    assert doc["count"] == 2 and len(doc["items"]) == 2 and doc["truncated"] is True


def test_nothing_is_written_to_disk_by_an_export(client, monkeypatch, tmp_path):
    _card()
    client.get("/api/memory/export?format=json", headers=_page())            # the stores' own first-open files, once
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
        r = client.get("/api/memory/export?format=" + fmt, headers=_page())
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
    r = client.get("/api/memory/export?" + qs, headers=_page())
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


# ── only Friday's own page can make the export (review finding: cross-site navigation) ──

#: What a browser sends when another site's page navigates the top-level window (or a link is
#: followed) to the export: no session token, and fetch metadata a page's script cannot forge.
CROSS_SITE_NAVIGATE = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                       "Sec-Fetch-User": "?1", "Referer": "https://evil.example/"}


@pytest.mark.parametrize("headers", [
    CROSS_SITE_NAVIGATE,
    {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
    {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
    {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"},
    {"Sec-Fetch-Site": "same-origin"},
    dict(CROSS_SITE_NAVIGATE, **{"X-Friday-Token": "not-the-token"}),
    {},
])
def test_a_request_not_from_fridays_page_gets_no_export(client, headers):
    _card()
    for qs in ("format=json", "format=md", "format=json&q=Wren"):
        r = client.get("/api/memory/export?" + qs, headers=headers)
        assert r.status_code == 403, (headers, qs, r.status_code)
        assert "attachment" not in r.headers.get("Content-Disposition", ""), headers
        assert "Wren" not in r.get_data(as_text=True), headers


def test_fridays_page_gets_the_export(client):
    _card()
    # apiFetch: the page's session token, on a same-origin script fetch.
    r = client.get("/api/memory/export?format=json", headers=dict(_page(), **{
        "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert r.headers["Content-Disposition"].startswith("attachment;")
    assert "Keeps a sailing boat called Wren." in r.get_data(as_text=True)
    # A same-origin script fetch whose token just rotated is still the page's own (fetch metadata).
    r = client.get("/api/memory/export?format=md", headers={"Sec-Fetch-Site": "same-origin",
                                                             "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
    assert r.status_code == 200 and "Wren" in r.get_data(as_text=True)


def test_the_panel_downloads_the_export_through_apifetch_not_a_link():
    body = _panel("ui_parts/app.html")
    i = body.index("'/api/memory/export?format='")
    assert body[max(0, i - 40):i].rstrip().endswith("apiFetch("), "the export is fetched with apiFetch"
    assert "r.blob()" in body and "URL.createObjectURL(b)" in body
    assert "href: '/api/memory/export" not in body and 'href="/api/memory/export' not in body
    assert "window.location" not in body and "window.open(" not in body


# ── the memory card's edit and delete refuse a line that moved (review finding: index race) ──

def _seen_then_moved():
    """The panel read "Keeps a sailing boat called Wren." at index 1; then the card moved under it."""
    cm.add("user", "Prefers tea to coffee.", actor="owner")
    cm.add("user", "Keeps a sailing boat called Wren.", actor="owner")
    cm.remove("user", "Prefers tea to coffee.", actor="friday")
    cm.add("user", "Walks to work on Mondays.", actor="friday")
    assert cm.entries("user") == ["Keeps a sailing boat called Wren.", "Walks to work on Mondays."]


@pytest.mark.parametrize("path, extra", [("edit", {"text": "Keeps a dinghy called Wren."}), ("delete", {})])
def test_a_card_edit_or_delete_of_a_line_that_moved_is_refused_with_409(client, path, extra):
    _seen_then_moved()
    r = client.post("/api/core-memory/" + path,
                    json=dict({"target": "user", "index": 1, "expect": "Keeps a sailing boat called Wren."}, **extra))
    assert r.status_code == 409, r.get_data(as_text=True)[:300]
    d = r.get_json()
    assert d["ok"] is False and "changed" in d["error"] and "reload" in d["error"], d
    assert cm.entries("user") == ["Keeps a sailing boat called Wren.", "Walks to work on Mondays."]
    # The same request naming the line where it now is goes through, and changes only that line.
    r = client.post("/api/core-memory/" + path,
                    json=dict({"target": "user", "index": 0, "expect": "Keeps a sailing boat called Wren."}, **extra))
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)[:300]
    assert "Walks to work on Mondays." in cm.entries("user")
    assert "Keeps a sailing boat called Wren." not in cm.entries("user")


def test_a_card_edit_or_delete_without_expect_behaves_as_before(client):
    _seen_then_moved()
    r = client.post("/api/core-memory/edit", json={"target": "user", "index": 1, "text": "Walks to work daily."})
    assert r.status_code == 200 and r.get_json()["ok"]
    r = client.post("/api/core-memory/delete", json={"target": "user", "index": 0})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert cm.entries("user") == ["Walks to work daily."]


def test_the_panel_sends_the_line_it_shows_with_each_card_edit_and_delete():
    for f in ("index.html", "ui_parts/app.html"):
        body = _panel(f)
        assert "json({target: at.target, index: at.index, text: text, expect: h.text})" in body, f
        assert "json({target: at.target, index: at.index, expect: h.text})" in body, f


# ── the memory card's routes are the owner's only (review finding: consistency) ──

@pytest.mark.parametrize("principal", ["observer", "paired_phone", "remote"])
@pytest.mark.parametrize("view, method, body", [
    ("get_core_memory", "GET", None),
    ("edit_line", "POST", {"target": "user", "index": 0, "text": "Keeps a dinghy called Wren."}),
    ("delete_line", "POST", {"target": "user", "index": 0}),
    ("review_suggestion", "POST", {"id": "x", "keep": True}),
])
def test_only_the_owners_own_principal_reads_or_changes_the_card(app, principal, view, method, body):
    from flask import g
    from agent_friday.routes import core_memory as routes
    _card()
    q = cm.queue("user", "add", text="Wren needs new sails before spring.", source="dream:2026-09-03")
    if body and "id" in body:
        body = dict(body, id=q["id"])
    before = (cm.entries("user"), [p.get("id") for p in cm.pending()])
    with app.test_request_context("/api/core-memory", method=method, json=body):
        g.friday_principal = principal
        r = getattr(routes, view).__wrapped__()
    resp = r[0] if isinstance(r, tuple) else r
    assert (r[1] if isinstance(r, tuple) else resp.status_code) == 403, (view, principal)
    assert "Wren" not in resp.get_data(as_text=True)
    assert (cm.entries("user"), [p.get("id") for p in cm.pending()]) == before


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
