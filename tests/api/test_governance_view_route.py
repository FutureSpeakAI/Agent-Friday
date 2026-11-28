"""The desktop draws the Governance workspace from its A2UI document, on this PC only.

    GET /api/liquid/views/governance/rendered  -> {ok, ws, label, rev, as_of, tree, look}
    GET /liquid/governance                     -> the standalone page that draws it

Held here: the tree is the reference renderer's bind of the builtin document with the broker's receipt rows; a
request from anywhere but this PC is refused before anything is read; and the device side never offers the view.
Synthetic data only.
"""
import pytest

from agent_friday.services import a2ui_render as rend
from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.services import governance_receipts as gr

REMOTE = {"REMOTE_ADDR": "192.0.2.10"}


@pytest.fixture(autouse=True)
def _views(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VIEWS_DIR", tmp_path / "liquid" / "views")
    monkeypatch.setattr(gr, "DB_PATH", tmp_path / "receipts.db")
    monkeypatch.setattr(gr, "_is_local", lambda provider, model: True)
    monkeypatch.setattr(gr, "sync_from_activity", lambda: 0)
    gr.reset()
    gr.put([gr.from_activity({"kind": "model_invocation", "ts": 1_800_000_000.0, "seq": 1,
                              "model": "sample-local:7b", "provider": "llama_cpp"})])


def test_the_desktop_tree_is_the_reference_bind_of_the_governance_document(client):
    r = client.get("/api/liquid/views/governance/rendered")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    want = rend.render(aws.builtin("governance"), aws.ctx("governance"), data={"governance": {
        "recent": [{"when": "2027-01-15T08:00:00Z", "what": "sample-local:7b answered on this PC",
                    "model": "sample-local:7b", "where": "on this PC"}],
        "models": [{"model": "sample-local:7b", "where": "on this PC", "turns": 1}]}})
    assert body["ok"] is True
    assert body["tree"] == want.tree
    assert (body["label"], body["rev"]) == ("Governance", aws.builtin_rev("governance"))


def test_the_governance_view_opens_on_this_pc_only(client, monkeypatch):
    """Even a caller the app lets in from elsewhere (a remote key and the page token) is refused, before any read."""
    from agent_friday import core
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "sample-remote-key")  # pragma: allowlist secret
    tok = {"X-Friday-Token": core._current_api_token()}
    read = []
    monkeypatch.setattr(gr, "recent", lambda **kw: read.append(kw) or [])
    r = client.get("/api/liquid/views/governance/rendered", environ_base=REMOTE, headers=tok)
    assert r.status_code == 403
    assert read == []
    assert b"sample-local" not in r.data
    assert client.get("/liquid/governance", environ_base=REMOTE, headers=tok).status_code == 403


def test_the_page_that_draws_it_opens_here(client):
    r = client.get("/liquid/governance")
    assert r.status_code == 200
    assert b"friday_a2ui.js" in r.data
