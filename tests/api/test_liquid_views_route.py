"""The desktop draws a workspace from the SAME A2UI document the phone draws (contract draft 3, section 4).

    GET /api/liquid/views/<ws>/rendered  -> {ok, ws, rev, as_of, tree, look}   (owner; bound by the reference renderer)
    GET /liquid/<ws>                     -> the standalone page that draws it with static/friday_a2ui.js (this PC only)

Held here: the tree is a2ui_render's bind of the stored-or-builtin document with the broker's data, with his
customization applied by the shared shell rule; a workspace with no document is 404; a document the broker refuses
never draws. Synthetic data only.
"""
import pytest

from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.tv_gateway import ws_knowledge

ROWS = [{"ref": "page:entities/sample-one.md", "title": "sample one", "line": "entities", "when": "2026-11-21T10:00:00Z"}]


@pytest.fixture(autouse=True)
def _views(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(store, "_admit_write", lambda: 0)
    monkeypatch.setattr(store, "_check_write", lambda origin: None)
    monkeypatch.setattr(store, "_announce", lambda view_id: None)
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: [dict(r) for r in ROWS])


def test_the_desktop_tree_is_the_reference_bind_of_the_same_document(client):
    r = client.get("/api/liquid/views/knowledge/rendered")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    want = rend.render(aws.builtin("knowledge"), aws.ctx("knowledge"),
                       data={"knowledge": {"sections": [{"name": "entities", "count": 1}],
                                           "recent": [{"title": "sample one", "section": "entities",
                                                       "when": "2026-11-21T10:00:00Z"}]}})
    assert body["tree"] == want.tree
    assert body["rev"] == aws.builtin_rev("knowledge")
    assert body["look"] == {"accent": None, "density": "comfortable", "summary": None}


def test_his_customization_from_the_phone_is_what_the_desktop_draws(client):
    aws.apply_change("knowledge", {"customization": {"hidden": ["recent"], "density": "compact"}},
                     base_rev=aws.builtin_rev("knowledge"), by="device:test")
    body = client.get("/api/liquid/views/knowledge/rendered").get_json()
    assert "recent_table" not in str(body["tree"]) and "sections_table" in str(body["tree"])
    assert body["look"]["density"] == "compact"


def test_a_workspace_with_no_document_is_404(client):
    assert client.get("/api/liquid/views/calendar/rendered").status_code == 404
    assert client.get("/api/liquid/views/..%2Fsecrets/rendered").status_code == 404


def test_the_page_loads_the_shared_renderer(client):
    # the browser end of this page is drawn in tests/app/specs/liquid_view_knowledge.spec.ts
    r = client.get("/liquid/knowledge")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/static/friday_a2ui.js" in html and "__FRIDAY_API_TOKEN" in html
    assert r.headers.get("Cache-Control") == "no-store"
    # Friday's own page, not a sandboxed one: a sandboxed page has an opaque origin and its fetch of the route fails
    csp = r.headers.get("Content-Security-Policy") or ""
    assert "sandbox" not in csp and "connect-src 'self'" in csp


def test_the_page_opens_on_this_pc_only(client):
    # signed in with the page's own token, so the refusal is the this-PC-only rule, not the sign-in
    from agent_friday import core
    signed = {"X-Friday-Token": core._current_api_token()}
    remote = {"REMOTE_ADDR": "203.0.113.7"}
    assert client.get("/api/liquid/views/knowledge/rendered", headers=signed, environ_base=remote).status_code == 200
    r = client.get("/liquid/knowledge", headers=signed, environ_base=remote)
    assert r.status_code == 403 and b"friday_a2ui.js" not in r.get_data()


def test_a_workspace_this_pc_does_not_serve_has_no_desktop_view(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    monkeypatch.setattr(ws_content, "_serving", lambda ws: None)
    assert client.get("/api/liquid/views/knowledge/rendered").status_code == 404


def test_an_unreadable_device_block_is_503_never_a_guessed_sensitivity(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content

    def broken(ws):
        raise OSError("manifest unreadable")
    monkeypatch.setattr(ws_content, "_serving", broken)
    seen = []
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: seen.append(sensitivity) or [])
    r = client.get("/api/liquid/views/knowledge/rendered")
    assert r.status_code == 503 and seen == []


def test_the_desktop_reads_with_the_workspaces_declared_sensitivity(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    real = ws_content._serving
    monkeypatch.setattr(ws_content, "_serving", lambda ws: (real(ws)[0], dict(real(ws)[1], sensitivity="private")))
    seen = []
    monkeypatch.setattr(ws_knowledge, "pages", lambda sensitivity: seen.append(sensitivity) or [dict(r) for r in ROWS])
    assert client.get("/api/liquid/views/knowledge/rendered").status_code == 200
    assert seen and set(seen) == {"private"}


def test_the_page_opens_only_where_its_data_call_answers(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content
    monkeypatch.setattr(ws_content, "_serving", lambda ws: None)
    assert client.get("/liquid/knowledge").status_code == 404


def test_the_page_is_503_when_the_device_block_cannot_be_read(client, monkeypatch):
    from agent_friday.tv_gateway import ws_content

    def broken(ws):
        raise OSError("manifest unreadable")
    monkeypatch.setattr(ws_content, "_serving", broken)
    r = client.get("/liquid/knowledge")
    assert r.status_code == 503 and b"friday_a2ui.js" not in r.get_data()
