"""The owner's memory search routes (routes/core_memory: /api/memory/search,
/api/memory/why): the owner only, parameters bounded.

Red on 51a160fcb: neither route existed (404 on every request below).
"""
from __future__ import annotations

import pytest

from agent_friday.services import core_memory as cm
from agent_friday.services import memory_influence as mi
from agent_friday.services import memory_search as ms
from agent_friday.services import observer_access as obs
from agent_friday.services import prompt_freeze

NON_LOOPBACK = "203.0.113.7"   # TEST-NET-3 (RFC 5737)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(mi, "_path", lambda: tmp_path / "recall-log.jsonl")
    monkeypatch.setattr(ms, "_forgotten_names", lambda: set())
    monkeypatch.setattr(ms, "_documents", lambda q: [])
    monkeypatch.setattr(ms, "_memory", lambda: None)
    prompt_freeze.reset()
    yield
    prompt_freeze.reset()


def test_the_owner_searches_and_asks_why(client):
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    r = client.get("/api/memory/search?q=Wren&kinds=card")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    d = r.get_json()
    assert d["ok"] and [x["text"] for x in d["results"]] == ["Keeps a sailing boat called Wren."]
    hit = d["results"][0]
    assert hit["status"] == "current" and hit["source"]["label"].startswith("Friday saved this")
    w = client.get("/api/memory/why", query_string={"kind": hit["kind"], "id": hit["id"]})
    assert w.status_code == 200 and w.get_json()["source"]["added_by"]["actor"] == "friday"


@pytest.mark.parametrize("qs", [
    "", "q=", "q=" + "x" * 201, "q=Wren&kinds=card,nope", "q=Wren&limit=0", "q=Wren&limit=51",
    "q=Wren&limit=lots", "q=Wren&since=yesterday", "q=Wren&until=2026-13",
])
def test_search_parameters_are_bounded(client, qs):
    r = client.get("/api/memory/search?" + qs)
    assert r.status_code == 400, (qs, r.status_code)
    assert r.get_json()["ok"] is False


@pytest.mark.parametrize("qs", ["", "kind=card", "kind=nope&id=x", "kind=card&id=" + "a" * 121,
                                "kind=card&id=..%2Fsecrets"])
def test_why_parameters_are_bounded(client, qs):
    r = client.get("/api/memory/why?" + qs)
    assert r.status_code == 400, (qs, r.status_code)


@pytest.mark.parametrize("path", ["/api/memory/search?q=Wren", "/api/memory/why?kind=card&id=card:user:abc"])
def test_a_caller_who_is_not_signed_in_gets_nothing(client, path):
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    r = client.get(path, environ_overrides={"REMOTE_ADDR": NON_LOOPBACK})
    assert r.status_code in (401, 403)
    assert "Wren" not in r.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/api/memory/search?q=Wren", "/api/memory/why?kind=card&id=card:user:abc"])
def test_an_observer_credential_never_reads_memory(client, monkeypatch, path):
    # Even if the observer allowlist ever let this route through, the route refuses it.
    cm.add("user", "Keeps a sailing boat called Wren.", actor="friday")
    monkeypatch.setattr(obs, "verify", lambda token: True)
    monkeypatch.setattr(obs, "is_read_allowed", lambda method, path: True)
    r = client.get(path, headers={obs.HEADER: "fobs_test"})
    assert r.status_code == 403
    assert "Wren" not in r.get_data(as_text=True)


def test_the_panel_is_in_both_ui_files():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    bodies = []
    for f in ("index.html", "ui_parts/app.html"):
        text = (root / f).read_text(encoding="utf-8")
        assert "function MemorySearchPanel(" in text, f
        assert "MemorySearchPanel" in text.split("function MemorySearchPanel(")[0], f + ": panel not mounted"
        assert "/api/memory/search" in text and "/api/memory/why" in text, f
        assert "Why do you know this?" in text and "Show corrected" in text, f
        body = text.split("function MemorySearchPanel(")[1].split("// End memory search panel")[0]
        bodies.append(body)
    assert bodies[0] == bodies[1], "the panel differs between index.html and ui_parts/app.html"
