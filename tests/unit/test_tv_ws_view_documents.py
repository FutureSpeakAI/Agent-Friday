"""Whole-document proposals for a workspace view (slice 3; ftv/program/specs/A2UI_SLICE3_OWNER_DOCUMENTS_2026-11-24.md).

    POST /tv/v1/workspaces/<ws>/view/proposals/challenge  {base_rev, change: {document, label}}
    POST /tv/v1/workspaces/<ws>/view/proposals            {..., nonce, time, sig}
    change: {"document": [...], "label": "..."} | {"reset": true}

Rules held here: a document he approves is checked as Friday's own documents are (composer, model_authored: no data
of its own, closed bindings, no verbs, reserved words), the same on save, restore, undo and reset; the record keeps
who approved it; a binding the device block does not serve is refused before anything is stored, so a view can never
be bricked; the body is read by the route itself, at most 72 KB, with duplicate keys, NaN and Infinity refused while
parsing; the first document change keeps the builtin as an earlier version; reset brings the builtin back as a new
version. Synthetic data only.
"""
import base64
import copy
import io
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import a2ui_store as store
from agent_friday.services import a2ui_workspaces as aws
from agent_friday.tv_gateway import approval_verify as av
from tests.unit.test_tv_pc_approve_decide import Enrolled, _clean, client
from tests.unit.test_tv_ws_view import VIEW, _views, fp, propose

_FIXTURES = (_clean, client, _views)    # pytest fixtures, imported by name so the tests below get them

WS = "knowledge"
CAP = 72 * 1024


def doc_with_title(text="My knowledge"):
    d = copy.deepcopy(aws.builtin(WS))
    for c in d[0]["createSurface"]["components"]:
        if c["id"] == "title":
            c["text"] = text
    return d


def _doc_change(doc, label="Retitled"):
    return {"document": doc, "label": label}


def _refused(r, rule=None):
    assert r.status_code == 400, (r.status_code, r.get_json())
    body = r.get_json()
    assert body["code"] in ("refused", "bad_request"), body
    if rule is not None:
        assert body.get("rule") == rule, body
    assert store.load(WS) is None, "nothing stored"


# ── accepted ───────────────────────────────────────────────────────────────

def test_an_approved_document_becomes_the_view_with_the_builtin_kept_before_it(client):
    dev = Enrolled(client)
    r, _ = propose(dev, _doc_change(doc_with_title()))
    assert r.status_code == 200, r.get_json()
    view = dev.get(VIEW).get_json()
    assert view["doc"] == doc_with_title() and view["rev"] == r.get_json()["rev"]
    versions = dev.get(VIEW + "/versions").get_json()["versions"]
    assert len(versions) >= 1, "the builtin is an earlier version"
    rec = store.load(WS)
    assert rec["authorship"] == "composer" and rec["model_authored"] is True, "checked as Friday's own documents"
    assert rec["approved_by"] == av.device_label(dev.id)


def test_his_customization_stays_and_hidden_parts_gone_from_the_new_document_are_named(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"customization": {"hidden": ["recent"], "density": "compact"}})
    assert r.status_code == 200, r.get_json()
    d = doc_with_title()
    comps = d[0]["createSurface"]["components"]
    comps[:] = [c for c in comps if c["id"] not in ("recent", "recent_table")]
    comps[0]["children"] = ["title", "browse", "sections"]
    del d[0]["createSurface"]["metadata"]["friday"]["bindings"]["recent"]
    r, _ = propose(dev, _doc_change(d))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["dropped_hidden"] == ["recent"]
    view = dev.get(VIEW).get_json()
    assert view["customization"].get("density") == "compact" and "recent" not in view["customization"].get("hidden", [])


# ── refused before anything is stored ──────────────────────────────────────

def test_a_document_that_plants_data_is_refused(client):
    d = doc_with_title()
    d.append({"version": "1.0", "updateDataModel": {"surfaceId": "workspace", "path": "/knowledge/sections",
                                                     "value": [{"name": "planted", "count": 99}]}})
    r, _ = propose(Enrolled(client), _doc_change(d))
    _refused(r, "V7")


def test_a_binding_this_workspace_does_not_serve_is_refused_and_the_view_still_answers(client):
    d = doc_with_title()
    d[0]["createSurface"]["metadata"]["friday"]["bindings"]["sections"] = {
        "path": "/knowledge/sections", "query": "futurespeak.projects", "params": {}}
    dev = Enrolled(client)
    r, _ = propose(dev, _doc_change(d))
    _refused(r)
    assert dev.get(VIEW).status_code == 200, "a refused document never bricks the view"


def test_a_verb_button_is_refused_in_a_document_he_approves(client):
    d = doc_with_title()
    comps = d[0]["createSurface"]["components"]
    comps.append({"id": "go", "component": "verb_button", "label": "Do it", "verb": "news.refresh"})
    comps[0]["children"].append("go")
    r, _ = propose(Enrolled(client), _doc_change(d))
    _refused(r)


def test_a_reserved_card_phrase_in_body_text_is_refused(client):
    r, _ = propose(Enrolled(client), _doc_change(doc_with_title("Approve this payment now")))
    _refused(r)


def test_a_label_must_be_short_plain_text(client):
    r, _ = propose(Enrolled(client), _doc_change(doc_with_title(), label="x" * 81))
    _refused(r)


def test_a_change_must_be_exactly_one_kind(client):
    r, _ = propose(Enrolled(client), {"document": doc_with_title(), "label": "a", "customization": {"density": "compact"}})
    _refused(r)


# ── the body: read by the route, capped, strict ───────────────────────────

def _raw(dev, path, data, headers=None):
    return dev.client.post(path, data=data, content_type="application/json", headers={**dev.session(), **(headers or {})})


def test_a_body_over_the_cap_is_413_before_it_is_parsed(client):
    dev = Enrolled(client)
    big = '{"base_rev": "r", "change": {"document": [], "label": "%s"}}' % ("x" * CAP)
    r = _raw(dev, VIEW + "/proposals/challenge", big)
    assert r.status_code == 413, r.status_code


def test_a_chunked_body_with_no_length_is_held_to_the_same_cap(client):
    dev = Enrolled(client)
    big = ('{"base_rev": "r", "change": {"document": [], "label": "%s"}}' % ("x" * CAP)).encode()
    r = dev.client.post(VIEW + "/proposals/challenge", input_stream=io.BytesIO(big), content_type="application/json",
                        headers={**dev.session(), "Transfer-Encoding": "chunked"},
                        environ_overrides={"wsgi.input_terminated": True})    # the server hands over a de-chunked body
    assert r.request.content_length is None, "sent with no length, as a chunked body is"
    assert r.status_code == 413, r.status_code


@pytest.mark.parametrize("raw", [
    '{"base_rev": "r", "base_rev": "s", "change": {"reset": true}}',
    '{"base_rev": "r", "change": {"reset": true, "reset": false}}',
    '{"base_rev": "r", "change": {"document": [{"version": "1.0", "version": "2.0"}], "label": "a"}}',
])
def test_a_repeated_key_is_refused_while_parsing(client, raw):
    r = _raw(Enrolled(client), VIEW + "/proposals/challenge", raw)
    assert r.status_code == 400 and r.get_json()["code"] == "bad_request", r.status_code


# ── reset ─────────────────────────────────────────────────────────────────

def test_reset_brings_the_builtin_back_as_a_new_version(client):
    dev = Enrolled(client)
    propose(dev, _doc_change(doc_with_title()))
    before = len(dev.get(VIEW + "/versions").get_json()["versions"])
    r, _ = propose(dev, {"reset": True})
    assert r.status_code == 200, r.get_json()
    view = dev.get(VIEW).get_json()
    assert view["doc"] == aws.builtin(WS)
    assert len(dev.get(VIEW + "/versions").get_json()["versions"]) == before + 1, "what he had is kept as a version"


def test_reset_with_nothing_stored_changes_nothing(client):
    dev = Enrolled(client)
    r, _ = propose(dev, {"reset": True})
    assert r.status_code == 200 and r.get_json()["rev"] == aws.builtin_rev(WS)
    assert store.load(WS) is None


def test_reset_still_works_after_more_versions_than_are_kept(client):
    dev = Enrolled(client)
    for i in range(store.MAX_VERSIONS + 2):
        r, _ = propose(dev, _doc_change(doc_with_title("Title %d" % i), label="v%d" % i))
        assert r.status_code == 200, (i, r.get_json())
    r, _ = propose(dev, {"reset": True})
    assert r.status_code == 200 and dev.get(VIEW).get_json()["doc"] == aws.builtin(WS)


# ── the same context on every path ────────────────────────────────────────

def test_restore_and_undo_check_as_strictly_as_save(client, monkeypatch):
    # a stored version that today's rules would refuse can't come back through restore or undo
    dev = Enrolled(client)
    propose(dev, _doc_change(doc_with_title("Fine")))
    rec = store.load(WS)
    bad = doc_with_title("Fine")
    bad.append({"version": "1.0", "updateDataModel": {"surfaceId": "workspace", "path": "/knowledge/sections",
                                                       "value": [{"name": "planted", "count": 1}]}})
    rec["versions"][-1]["doc"] = bad
    store._write_atomic(store._path(WS), json.dumps(rec).encode("utf-8"))
    vid = rec["versions"][-1]["id"]
    r, _ = propose(dev, {"restore": vid})
    assert r.status_code == 400 and r.get_json()["rule"] == "V7", r.get_json()
    with pytest.raises(store.ViewRefused):
        store.undo(WS, base_rev=store.load(WS)["rev"], by="test")
